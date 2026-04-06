"""Step 2: Parallel retrieval via LightRAG aquery_data()."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from lightrag import QueryParam

logger = logging.getLogger(__name__)


def _summarize_retrieval(key: str, result: dict[str, Any]) -> None:
    """Log a human-readable summary of what was retrieved for a given query."""
    if result.get("status") != "success":
        logger.info("  [%s] retrieval failed or empty", key)
        return

    data = result.get("data", {})
    entities = data.get("entities", [])
    relationships = data.get("relationships", [])
    chunks = data.get("chunks", [])

    # Summarize entity types
    type_counts: dict[str, int] = {}
    entity_names: list[str] = []
    for e in entities:
        etype = e.get("entity_type", "unknown")
        type_counts[etype] = type_counts.get(etype, 0) + 1
        entity_names.append(e.get("entity_name", "?"))
    type_summary = ", ".join(f"{t}:{c}" for t, c in sorted(type_counts.items()))

    # Summarize chunk content (first line of each)
    chunk_previews: list[str] = []
    for c in chunks[:5]:
        content = c.get("content", "").strip()
        first_line = content.split("\n")[0][:80] if content else "(empty)"
        chunk_previews.append(first_line)

    logger.info("  [%s] %d entities {%s}, %d relations, %d chunks",
                key, len(entities), type_summary, len(relationships), len(chunks))
    if entity_names:
        logger.info("  [%s] top entities: %s", key, ", ".join(entity_names[:10]))
    for i, preview in enumerate(chunk_previews):
        logger.info("  [%s] chunk[%d]: %s", key, i, preview)


def _format_retrieval_block(result: dict[str, Any]) -> str:
    """Convert aquery_data result into a readable text block for the LLM planner."""
    if result.get("status") != "success":
        return "(no data retrieved)"

    data = result.get("data", {})
    parts: list[str] = []

    # Chunks carry the richest context
    for chunk in data.get("chunks", []):
        content = chunk.get("content", "").strip()
        if content:
            parts.append(content)

    # Entities provide structured info
    for entity in data.get("entities", []):
        desc = entity.get("description", "").strip()
        if desc:
            name = entity.get("entity_name", "")
            etype = entity.get("entity_type", "")
            parts.append(f"[{etype}] {name}: {desc}")

    # Relationships provide connections
    for rel in data.get("relationships", []):
        desc = rel.get("description", "").strip()
        if desc:
            parts.append(desc)

    # Limit total output to avoid oversized LLM prompts
    text = "\n\n".join(parts) if parts else "(no data retrieved)"
    max_chars = 8000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n... (truncated)"
    return text


async def parallel_retrieve(
    rag,
    date: str,
    top_k: int = 30,
    chunk_top_k: int = 10,
) -> dict[str, str]:
    """Fire 4 parallel queries to LightRAG and return formatted text blocks.

    Returns a dict with keys: route_data, event_data, notice_data, story_data.
    Each value is a text block ready to embed in the planner prompt.
    """
    # Pre-supply keywords to bypass LightRAG's LLM keyword extraction step.
    # This avoids the structured output API call that some models don't support,
    # and saves ~1-2s per query.
    queries = {
        "route_data": (
            "3小时 首次来馆 国宝 精华路线 推荐路线 必看文物 展览",
            QueryParam(
                mode="mix", top_k=top_k, chunk_top_k=chunk_top_k,
                hl_keywords=["博物馆路线", "参观规划", "国宝精华"],
                ll_keywords=["3小时", "路线", "国宝", "精华", "首次", "必看", "文物", "展览", "推荐"],
            ),
        ),
        "event_data": (
            f"{date} 今天 活动 讲解 导览 工作坊 体验 集章",
            QueryParam(
                mode="mix", top_k=top_k, chunk_top_k=chunk_top_k,
                hl_keywords=["博物馆活动", "今日讲解导览"],
                ll_keywords=["活动", "讲解", "导览", "工作坊", "体验", "集章", "志愿者", date],
            ),
        ),
        "notice_data": (
            f"{date} 今天 关闭 维修 限流 排队 闭馆 注意事项",
            QueryParam(
                mode="mix", top_k=top_k, chunk_top_k=chunk_top_k,
                hl_keywords=["运营公告", "注意事项"],
                ll_keywords=["关闭", "维修", "限流", "排队", "闭馆", "注意", "电梯", "门票", date],
            ),
        ),
        "story_data": (
            "咖啡 文创 冷知识 打卡 拍照 纪念品 趣味 小红书",
            QueryParam(
                mode="mix", top_k=top_k, chunk_top_k=chunk_top_k,
                hl_keywords=["趣味内容", "文创购物", "冷知识"],
                ll_keywords=["咖啡", "文创", "冷知识", "打卡", "拍照", "纪念品", "小红书", "盲盒"],
            ),
        ),
    }

    async def _do_query(key: str, query: str, param: QueryParam) -> tuple[str, dict]:
        try:
            result = await rag.aquery_data(query, param=param)
            logger.info(
                "Query [%s] returned %d entities, %d relations, %d chunks",
                key,
                len(result.get("data", {}).get("entities", [])),
                len(result.get("data", {}).get("relationships", [])),
                len(result.get("data", {}).get("chunks", [])),
            )
            return key, result
        except Exception:
            logger.exception("Query [%s] failed", key)
            return key, {"status": "failure", "data": {}}

    # Fire all queries in parallel
    tasks = [_do_query(key, q, p) for key, (q, p) in queries.items()]
    results = await asyncio.gather(*tasks)

    # Log detailed summaries and format each result into a text block
    logger.info("=" * 50)
    logger.info("Retrieval Results Summary")
    logger.info("=" * 50)
    formatted = {}
    for key, result in results:
        _summarize_retrieval(key, result)
        formatted[key] = _format_retrieval_block(result)
        logger.info("  [%s] formatted text: %d chars", key, len(formatted[key]))
    logger.info("=" * 50)

    return formatted
