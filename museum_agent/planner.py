"""Step 3: LLM-based planning and generation."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from collections.abc import AsyncIterator

from museum_agent.llm import llm_complete, llm_complete_stream
from museum_agent.models import Plan, PlanStop, UserIntent
from museum_agent.prompts import PLANNER_SYSTEM_PROMPT, PLANNER_USER_PROMPT, VALIDATION_FIX_PROMPT

logger = logging.getLogger(__name__)


def _build_planner_messages(
    intent: UserIntent,
    retrieval: dict[str, str],
) -> list[dict[str, str]]:
    """Build the chat messages for the planner LLM call."""
    user_msg = PLANNER_USER_PROMPT.format(
        date=intent.date,
        time_budget_min=intent.time_budget_min,
        start_time=intent.start_time,
        audience=intent.audience,
        has_child=intent.has_child,
        has_elderly=intent.has_elderly,
        interests=", ".join(intent.interests) if intent.interests else "not specified",
        route_data=retrieval.get("route_data", "(no data)"),
        event_data=retrieval.get("event_data", "(no data)"),
        notice_data=retrieval.get("notice_data", "(no data)"),
        story_data=retrieval.get("story_data", "(no data)"),
    )
    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]


def _build_fix_messages(
    errors: list[str],
    plan_json_str: str,
    original_messages: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Build messages for a validation-fix retry."""
    fix_msg = VALIDATION_FIX_PROMPT.format(
        errors="\n".join(f"- {e}" for e in errors),
        plan_json=plan_json_str,
    )
    return original_messages + [{"role": "user", "content": fix_msg}]


def parse_llm_response(text: str) -> tuple[dict[str, Any] | None, str]:
    """Parse the LLM response into (plan_dict, plan_text).

    The LLM is instructed to output:
      ---PLAN_JSON---
      {...json...}
      ---PLAN_TEXT---
      ...natural language...
    """
    plan_dict = None
    plan_text = text  # fallback: entire response

    # Extract JSON section
    json_match = re.search(
        r"---PLAN_JSON---\s*(.*?)\s*---PLAN_TEXT---",
        text,
        re.DOTALL,
    )
    if json_match:
        json_str = json_match.group(1).strip()
        # Strip markdown code fences if present
        json_str = re.sub(r"^```(?:json)?\s*", "", json_str)
        json_str = re.sub(r"\s*```$", "", json_str)
        try:
            plan_dict = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Failed to parse Plan JSON from LLM response")

    # Extract text section
    text_match = re.search(r"---PLAN_TEXT---\s*(.*)", text, re.DOTALL)
    if text_match:
        plan_text = text_match.group(1).strip()

    return plan_dict, plan_text


def dict_to_plan(d: dict[str, Any]) -> Plan:
    """Convert a raw dict to a Plan dataclass."""
    stops = []
    for s in d.get("stops", []):
        stops.append(PlanStop(
            zone_id=s.get("zone_id", ""),
            zone_name=s.get("zone_name", ""),
            arrive_time=s.get("arrive_time", ""),
            depart_time=s.get("depart_time", ""),
            duration_min=s.get("duration_min", 0),
            anchor_event=s.get("anchor_event"),
            artifacts=s.get("artifacts", []),
            notices=s.get("notices", []),
            stories=s.get("stories", []),
        ))
    return Plan(
        stops=stops,
        entrance_hint=d.get("entrance_hint", ""),
        total_duration_min=d.get("total_duration_min", 0),
        skipped_events=d.get("skipped_events", []),
        post_visit_tips=d.get("post_visit_tips", []),
    )


async def run_planner(
    llm_model: str,
    intent: UserIntent,
    retrieval: dict[str, str],
) -> tuple[Plan | None, str, str]:
    """Call the LLM to generate a tour plan.

    Returns (plan, plan_text, raw_response).
    - plan: parsed Plan object (or None if parsing failed)
    - plan_text: the natural language itinerary
    - raw_response: full LLM output
    """
    messages = _build_planner_messages(intent, retrieval)

    # The user message contains all context; system prompt has instructions
    user_content = messages[1]["content"]
    logger.info("Planner input summary:")
    logger.info("  System prompt: %d chars", len(PLANNER_SYSTEM_PROMPT))
    logger.info("  User prompt: %d chars", len(user_content))
    logger.info("  User info: budget=%dmin, start=%s, audience=%s",
                intent.time_budget_min, intent.start_time, intent.audience)
    for key in ["route_data", "event_data", "notice_data", "story_data"]:
        data_block = retrieval.get(key, "")
        # Show first 100 chars of each block as preview
        preview = data_block[:100].replace("\n", " ") + "..." if len(data_block) > 100 else data_block.replace("\n", " ")
        logger.info("  [%s] %d chars: %s", key, len(data_block), preview)

    raw = await llm_complete(
        prompt=user_content,
        system_prompt=PLANNER_SYSTEM_PROMPT,
        model=llm_model,
    )

    logger.info("Planner LLM response: %d chars", len(raw))
    plan_dict, plan_text = parse_llm_response(raw)

    if plan_dict:
        n_stops = len(plan_dict.get("stops", []))
        logger.info("  Parsed Plan JSON: %d stops, total %d min",
                     n_stops, plan_dict.get("total_duration_min", 0))
    else:
        logger.warning("  Failed to parse Plan JSON from LLM response")
        # Log a snippet of the raw response to help debug
        logger.warning("  Response starts with: %s", raw[:200].replace("\n", " "))

    plan = dict_to_plan(plan_dict) if plan_dict else None
    return plan, plan_text, raw


async def run_planner_stream(
    llm_model: str,
    intent: UserIntent,
    retrieval: dict[str, str],
) -> AsyncIterator[str]:
    """Stream the planner output. Yields raw text chunks as they arrive.

    The caller should collect all chunks to form the full response,
    then call parse_llm_response() on the accumulated text.
    """
    messages = _build_planner_messages(intent, retrieval)
    user_content = messages[1]["content"]

    logger.info("Planner input summary (stream mode):")
    logger.info("  System prompt: %d chars", len(PLANNER_SYSTEM_PROMPT))
    logger.info("  User prompt: %d chars", len(user_content))
    logger.info("  User info: budget=%dmin, start=%s, audience=%s",
                intent.time_budget_min, intent.start_time, intent.audience)

    async for chunk in llm_complete_stream(
        prompt=user_content,
        system_prompt=PLANNER_SYSTEM_PROMPT,
        model=llm_model,
    ):
        yield chunk


async def run_planner_fix(
    llm_model: str,
    errors: list[str],
    plan_json_str: str,
    original_messages: list[dict[str, str]],
) -> tuple[Plan | None, str, str]:
    """Retry the planner with validation errors attached."""
    messages = _build_fix_messages(errors, plan_json_str, original_messages)
    prompt_text = "\n".join(m["content"] for m in messages)

    raw = await llm_complete(
        prompt=prompt_text,
        system_prompt=PLANNER_SYSTEM_PROMPT,
        model=llm_model,
    )

    plan_dict, plan_text = parse_llm_response(raw)
    plan = dict_to_plan(plan_dict) if plan_dict else None

    return plan, plan_text, raw
