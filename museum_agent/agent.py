"""Main MuseumAgent class orchestrating the 4-step flow."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from dotenv import load_dotenv

from lightrag import LightRAG
from lightrag.base import QueryParam
from lightrag.llm.openai import openai_complete, openai_embed
from lightrag.utils import EmbeddingFunc

from museum_agent.llm import llm_complete
from museum_agent.models import Plan, UserIntent
from museum_agent.planner import (
    dict_to_plan,
    parse_llm_response,
    run_planner,
    run_planner_fix,
    run_planner_stream,
)
from museum_agent.prompts import INTENT_PARSE_PROMPT
from museum_agent.retrieval import parallel_retrieve
from museum_agent.validator import validate_plan

logger = logging.getLogger(__name__)


class MuseumAgent:
    """Museum AI Tour Guide Agent.

    Implements the 4-step flow:
      Step 1: Intent parsing
      Step 2: Parallel retrieval via LightRAG
      Step 3: LLM planning + generation
      Step 4: Code validation (with optional LLM retry)
    """

    def __init__(self, env_path: str | None = None):
        """Initialize the agent. Call `await agent.setup()` before use."""
        # Load environment
        if env_path:
            load_dotenv(env_path, override=False)
        else:
            # Try common locations
            for candidate in [".env", os.path.join(os.path.dirname(__file__), "..", ".env")]:
                if os.path.exists(candidate):
                    load_dotenv(candidate, override=False)
                    break

        self.llm_model = os.getenv("LLM_MODEL", "qwen3.5-plus")
        self.llm_host = os.getenv("LLM_BINDING_HOST", "")
        self.llm_api_key = os.getenv("LLM_BINDING_API_KEY", "")
        self.rag: LightRAG | None = None

    async def setup(self):
        """Initialize LightRAG and storage connections."""
        embedding_api_key = os.getenv("EMBEDDING_BINDING_API_KEY", "")
        embedding_host = os.getenv("EMBEDDING_BINDING_HOST", "https://api.openai.com/v1")
        embedding_model = os.getenv("EMBEDDING_MODEL", "bge-m3")
        embedding_dim = int(os.getenv("EMBEDDING_DIM", "1024"))

        async def _embedding_func(texts: list[str]) -> list:
            return await openai_embed.func(
                texts,
                model=embedding_model,
                base_url=embedding_host,
                api_key=embedding_api_key,
                embedding_dim=embedding_dim,
            )

        self.rag = LightRAG(
            working_dir=os.getenv("WORKING_DIR", "./rag_storage"),
            llm_model_func=openai_complete,
            llm_model_name=self.llm_model,
            embedding_func=EmbeddingFunc(
                embedding_dim=embedding_dim,
                max_token_size=int(os.getenv("EMBEDDING_TOKEN_LIMIT", "8192")),
                func=_embedding_func,
                model_name=embedding_model,
            ),
            kv_storage=os.getenv("LIGHTRAG_KV_STORAGE", "PGKVStorage"),
            graph_storage=os.getenv("LIGHTRAG_GRAPH_STORAGE", "PGGraphStorage"),
            vector_storage=os.getenv("LIGHTRAG_VECTOR_STORAGE", "PGVectorStorage"),
            doc_status_storage=os.getenv("LIGHTRAG_DOC_STATUS_STORAGE", "PGDocStatusStorage"),
        )
        await self.rag.initialize_storages()
        logger.info("MuseumAgent initialized (LLM=%s)", self.llm_model)

    async def teardown(self):
        """Close storage connections."""
        if self.rag:
            await self.rag.finalize_storages()

    # ------------------------------------------------------------------
    # Step 1: Intent parsing
    # ------------------------------------------------------------------
    async def parse_intent(self, user_message: str, date: str) -> UserIntent:
        """Parse user's natural language input into structured intent."""
        prompt = INTENT_PARSE_PROMPT.format(date=date, user_message=user_message)

        try:
            raw = await llm_complete(prompt, model=self.llm_model)
            # Strip markdown code fences if present
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[: cleaned.rfind("```")]
            # Remove <think>...</think> blocks (qwen thinking mode)
            import re
            cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()

            data = json.loads(cleaned)
            intent = UserIntent(
                time_budget_min=data.get("time_budget_min", 180),
                start_time=data.get("start_time", "09:00"),
                date=date,
                audience=data.get("audience", "adult_solo"),
                has_child=data.get("has_child", False),
                has_elderly=data.get("has_elderly", False),
                interests=data.get("interests", []),
                raw_query=user_message,
            )
            logger.info("Parsed intent: %s", intent)
            return intent
        except Exception:
            logger.exception("Intent parsing failed, using defaults")
            return UserIntent(date=date, raw_query=user_message)

    # ------------------------------------------------------------------
    # Step 2: Parallel retrieval
    # ------------------------------------------------------------------
    async def retrieve(self, date: str) -> dict[str, str]:
        """Run parallel retrieval queries against LightRAG."""
        assert self.rag is not None, "Call setup() first"
        return await parallel_retrieve(self.rag, date=date)

    # ------------------------------------------------------------------
    # Step 3: LLM planning + generation
    # ------------------------------------------------------------------
    async def plan(
        self, intent: UserIntent, retrieval: dict[str, str]
    ) -> tuple[Plan | None, str, str]:
        """Generate the tour plan via LLM."""
        return await run_planner(
            llm_model=self.llm_model,
            intent=intent,
            retrieval=retrieval,
        )

    # ------------------------------------------------------------------
    # Step 4: Validation + optional retry
    # ------------------------------------------------------------------
    def validate(self, plan: Plan, intent: UserIntent) -> list[str]:
        """Validate the plan with code-based checks."""
        return validate_plan(plan, intent.time_budget_min, intent.start_time)

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------
    async def run(self, user_message: str, date: str) -> dict[str, Any]:
        """Execute the full 4-step pipeline.

        Returns a dict with:
          - plan_text: natural language itinerary for the user
          - plan: Plan object (or None)
          - validation_errors: list of errors (empty if valid)
          - timings: dict of step durations in seconds
        """
        timings: dict[str, float] = {}

        # Step 1 + Step 2: Run intent parsing and retrieval in parallel
        logger.info("=" * 60)
        logger.info("[Step 1+2] Starting intent parsing + parallel retrieval")
        logger.info("=" * 60)
        t0 = time.monotonic()
        import asyncio
        intent_task = asyncio.create_task(self.parse_intent(user_message, date))
        retrieval_task = asyncio.create_task(self.retrieve(date))
        intent, retrieval = await asyncio.gather(intent_task, retrieval_task)
        timings["step1_2_intent_and_retrieval"] = time.monotonic() - t0
        logger.info("[Step 1+2] Done in %.2fs", timings["step1_2_intent_and_retrieval"])
        logger.info("  Intent: budget=%dmin, start=%s, audience=%s, child=%s, elderly=%s, interests=%s",
                     intent.time_budget_min, intent.start_time, intent.audience,
                     intent.has_child, intent.has_elderly, intent.interests)
        for key in ["route_data", "event_data", "notice_data", "story_data"]:
            chars = len(retrieval.get(key, ""))
            logger.info("  Retrieval [%s]: %d chars", key, chars)

        # Step 3: LLM planning + generation
        logger.info("=" * 60)
        logger.info("[Step 3] Starting LLM planning + generation")
        logger.info("=" * 60)
        t0 = time.monotonic()
        plan, plan_text, raw_response = await self.plan(intent, retrieval)
        timings["step3_planning"] = time.monotonic() - t0
        logger.info("[Step 3] Done in %.2fs", timings["step3_planning"])
        logger.info("  Plan JSON parsed: %s", "yes" if plan else "NO")
        logger.info("  Plan text length: %d chars", len(plan_text))
        logger.info("  Raw LLM response: %d chars", len(raw_response))

        # Step 4: Validation
        logger.info("=" * 60)
        logger.info("[Step 4] Starting code validation")
        logger.info("=" * 60)
        t0 = time.monotonic()
        errors: list[str] = []
        if plan:
            errors = self.validate(plan, intent)
            if errors:
                logger.warning("[Step 4] Validation errors: %s", errors)
                # One retry attempt
                plan_json_str = json.dumps(
                    {
                        "stops": [
                            {
                                "zone_id": s.zone_id,
                                "zone_name": s.zone_name,
                                "arrive_time": s.arrive_time,
                                "depart_time": s.depart_time,
                                "duration_min": s.duration_min,
                                "anchor_event": s.anchor_event,
                                "artifacts": s.artifacts,
                                "notices": s.notices,
                                "stories": s.stories,
                            }
                            for s in plan.stops
                        ],
                        "entrance_hint": plan.entrance_hint,
                        "total_duration_min": plan.total_duration_min,
                        "skipped_events": plan.skipped_events,
                        "post_visit_tips": plan.post_visit_tips,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                plan2, plan_text2, _ = await run_planner_fix(
                    llm_model=self.llm_model,
                    errors=errors,
                    plan_json_str=plan_json_str,
                    original_messages=[],
                )
                if plan2:
                    errors2 = self.validate(plan2, intent)
                    if len(errors2) < len(errors):
                        plan = plan2
                        plan_text = plan_text2
                        errors = errors2
                        logger.info("[Step 4] Retry improved plan (%d → %d errors)", len(errors), len(errors2))
        else:
            logger.warning("[Step 4] No Plan JSON parsed, skipping validation")

        timings["step4_validation"] = time.monotonic() - t0
        timings["total"] = sum(timings.values())
        logger.info(
            "[Done] Total %.2fs (step1+2=%.2f, planning=%.2f, validation=%.2f)",
            timings["total"],
            timings["step1_2_intent_and_retrieval"],
            timings["step3_planning"],
            timings["step4_validation"],
        )

        return {
            "plan_text": plan_text,
            "plan": plan,
            "validation_errors": errors,
            "timings": timings,
            "intent": intent,
        }

    # ------------------------------------------------------------------
    # Streaming pipeline
    # ------------------------------------------------------------------
    async def run_stream(self, user_message: str, date: str):
        """Execute the pipeline with streaming output.

        Yields tuples of (event_type, data):
          ("status", "message")     - progress updates
          ("chunk", "text")         - streaming plan text
          ("plan_json", plan_dict)  - parsed plan JSON (after stream ends)
          ("validation", errors)    - validation results
          ("timings", timings_dict) - timing info
        """
        import asyncio
        timings: dict[str, float] = {}

        # Step 1 + Step 2: parallel
        yield ("status", "Parsing intent and retrieving data...")
        t0 = time.monotonic()
        intent_task = asyncio.create_task(self.parse_intent(user_message, date))
        retrieval_task = asyncio.create_task(self.retrieve(date))
        intent, retrieval = await asyncio.gather(intent_task, retrieval_task)
        timings["step1_2_intent_and_retrieval"] = time.monotonic() - t0

        yield ("status", f"Intent: {intent.audience}, {intent.time_budget_min}min from {intent.start_time}")
        for key in ["route_data", "event_data", "notice_data", "story_data"]:
            chars = len(retrieval.get(key, ""))
            yield ("status", f"  Retrieved [{key}]: {chars} chars")

        # Step 3: Streaming LLM generation
        yield ("status", "Generating tour plan...")
        t0 = time.monotonic()
        raw_chunks: list[str] = []
        in_plan_text = False

        async for chunk in run_planner_stream(
            llm_model=self.llm_model,
            intent=intent,
            retrieval=retrieval,
        ):
            raw_chunks.append(chunk)
            accumulated = "".join(raw_chunks)

            # Start streaming to user once we hit ---PLAN_TEXT---
            if not in_plan_text and "---PLAN_TEXT---" in accumulated:
                in_plan_text = True
                # Emit everything after the marker
                after_marker = accumulated.split("---PLAN_TEXT---", 1)[1]
                if after_marker:
                    yield ("chunk", after_marker)
            elif in_plan_text:
                yield ("chunk", chunk)

        timings["step3_planning"] = time.monotonic() - t0

        # Parse the full response
        raw_response = "".join(raw_chunks)
        plan_dict, plan_text = parse_llm_response(raw_response)
        plan = dict_to_plan(plan_dict) if plan_dict else None

        if plan_dict:
            yield ("plan_json", plan_dict)

        # Step 4: Validation
        t0 = time.monotonic()
        errors: list[str] = []
        if plan:
            errors = self.validate(plan, intent)
        timings["step4_validation"] = time.monotonic() - t0
        timings["total"] = sum(timings.values())

        yield ("validation", errors)
        yield ("timings", timings)
