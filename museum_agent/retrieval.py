"""Step 2: Parallel retrieval via LightRAG aquery_data()."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from lightrag import QueryParam

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# User keyword extraction (pure code, no LLM needed)
# ---------------------------------------------------------------------------

# Interest domains and their associated search keywords.
# key: regex pattern to match in user message
# value: (extra query words, extra ll_keywords)
INTEREST_PATTERNS: list[tuple[str, list[str], list[str]]] = [
    # Art categories
    (r"青铜", ["青铜器 鼎 尊 铸造"], ["青铜器", "鼎", "尊", "铸造", "商代"]),
    (r"瓷器|陶瓷|瓷", ["瓷器 釉 窑 青花"], ["瓷器", "釉", "窑", "青花", "转心瓶"]),
    (r"书画|字画|国画|书法", ["书画 卷轴 画卷 文人"], ["书画", "文徵明", "乾隆南巡图", "郑板桥"]),
    (r"玉器|玉", ["玉器 玉龙 玉琮 玉佩"], ["玉器", "玉龙", "玉琮", "良渚", "红山"]),
    (r"佛|造像|菩萨|观音", ["佛造像 观音 菩萨"], ["佛造像", "观音", "菩萨", "木雕"]),
    (r"钱币|货币|古钱", ["钱币 刀币 铜币"], ["钱币", "刀币", "铜币", "货币"]),
    (r"庞贝|罗马|特展|壁画", ["庞贝 特展 壁画 雕像 古罗马"], ["庞贝", "壁画", "维纳斯", "特展"]),
    # Audience hints
    (r"孩子|儿童|小朋友|亲子|带娃", ["亲子 儿童 工作坊 互动"], ["亲子", "儿童", "工作坊", "互动体验"]),
    (r"老人|老年|长辈|爸妈|父母", ["无障碍 电梯 休息"], ["电梯", "休息", "无障碍"]),
    # Experience preferences
    (r"拍照|打卡|出片|拍摄", ["打卡 拍照 机位 出片"], ["打卡", "拍照", "机位", "出片", "小红书"]),
    (r"文创|纪念品|手办|盲盒", ["文创 纪念品 盲盒 限量"], ["文创", "纪念品", "盲盒", "限量", "联名"]),
    (r"咖啡|餐饮|吃|喝", ["咖啡 餐饮 食堂 拉花"], ["咖啡", "餐饮", "食堂", "拉花"]),
    (r"讲解|导览|导游", ["讲解 导览 志愿者"], ["讲解", "导览", "志愿者", "导游"]),
]

# Time budget patterns
TIME_PATTERNS: list[tuple[str, str]] = [
    (r"(\d+)\s*小时", "hour"),
    (r"(\d+)\s*h", "hour"),
    (r"半天", "half_day"),
    (r"一整天|全天", "full_day"),
]


def extract_user_keywords(user_message: str) -> dict[str, Any]:
    """Extract retrieval-relevant signals from the raw user message.

    Returns a dict with:
      - interest_query_words: list[str] extra words to add to route/story queries
      - interest_ll_keywords: list[str] extra low-level keywords
      - audience_query_words: list[str] extra words for event queries
      - audience_ll_keywords: list[str] extra low-level keywords for events
      - time_hint: str | None  (e.g. "2小时", "半天")
      - matched_patterns: list[str] which patterns matched (for logging)
    """
    result: dict[str, Any] = {
        "interest_query_words": [],
        "interest_ll_keywords": [],
        "audience_query_words": [],
        "audience_ll_keywords": [],
        "time_hint": None,
        "matched_patterns": [],
    }

    for pattern, query_words, ll_keywords in INTEREST_PATTERNS:
        if re.search(pattern, user_message):
            result["matched_patterns"].append(pattern)
            # Audience-related patterns go to event queries
            if pattern in (r"孩子|儿童|小朋友|亲子|带娃", r"老人|老年|长辈|爸妈|父母"):
                result["audience_query_words"].extend(query_words)
                result["audience_ll_keywords"].extend(ll_keywords)
            else:
                result["interest_query_words"].extend(query_words)
                result["interest_ll_keywords"].extend(ll_keywords)

    # Time extraction
    for pattern, kind in TIME_PATTERNS:
        m = re.search(pattern, user_message)
        if m:
            if kind == "hour":
                result["time_hint"] = f"{m.group(1)}小时"
            elif kind == "half_day":
                result["time_hint"] = "半天"
            elif kind == "full_day":
                result["time_hint"] = "全天"
            break

    return result


# ---------------------------------------------------------------------------
# Retrieval result formatting
# ---------------------------------------------------------------------------

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


def _check_chunk_temporal(content: str, today: str) -> tuple[str, bool]:
    """Check temporal validity of a chunk and return (annotated_content, is_valid).

    Chunks have prefixes like:
      【2026-04-05 15:00-16:00｜B1北区｜提示】
      【2026-04-01至2026-04-30｜游客接待台｜活动】
      【2026-04-03发布｜B1北区｜curator_insight】

    Returns:
      - annotated content with [有效] prefix (for valid chunks)
      - is_valid: False if the chunk is expired and should be filtered out
    """
    # Pattern 1: single date with time 【2026-04-05 15:00-16:00｜...】
    m = re.match(r"【(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}", content)
    if m:
        chunk_date = m.group(1)
        if chunk_date < today:
            return content, False  # expired, filter out
        elif chunk_date == today:
            return f"[今日{today}] {content}", True
        else:
            return f"[适用于{chunk_date}] {content}", True

    # Pattern 2: date range 【2026-04-05至2026-04-06｜...】
    m = re.match(r"【(\d{4}-\d{2}-\d{2})至(\d{4}-\d{2}-\d{2})｜", content)
    if m:
        start_date, end_date = m.group(1), m.group(2)
        if end_date < today:
            return content, False  # expired, filter out
        elif start_date <= today <= end_date:
            return f"[有效期至{end_date}] {content}", True
        else:
            return f"[未生效：{start_date}起] {content}", True  # keep, LLM may want to mention upcoming

    # Pattern 3: published date 【2026-04-03发布｜...】 (stories — always valid)
    m = re.match(r"【(\d{4}-\d{2}-\d{2})发布｜", content)
    if m:
        return content, True

    # No date prefix found (static content) — always valid
    return content, True


def _format_retrieval_block(result: dict[str, Any], today: str = "") -> str:
    """Convert aquery_data result into a readable text block for the LLM planner."""
    if result.get("status") != "success":
        return "(no data retrieved)"

    data = result.get("data", {})
    parts: list[str] = []

    # Chunks carry the richest context — filter out expired ones
    expired_count = 0
    for chunk in data.get("chunks", []):
        content = chunk.get("content", "").strip()
        if not content:
            continue
        if today:
            content, is_valid = _check_chunk_temporal(content, today)
            if not is_valid:
                expired_count += 1
                continue
        parts.append(content)
    if expired_count:
        logger.info("  Filtered out %d expired chunks (today=%s)", expired_count, today)

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


# ---------------------------------------------------------------------------
# Main retrieval function
# ---------------------------------------------------------------------------

async def parallel_retrieve(
    rag,
    date: str,
    user_message: str = "",
    top_k: int = 30,
    chunk_top_k: int = 10,
) -> dict[str, str]:
    """Fire 4 parallel queries to LightRAG and return formatted text blocks.

    The user_message is analyzed to extract interest/audience keywords,
    which are injected into the retrieval queries for better relevance.

    Returns a dict with keys: route_data, event_data, notice_data, story_data.
    Each value is a text block ready to embed in the planner prompt.
    """
    # Extract keywords from user message (pure code, ~0ms)
    user_kw = extract_user_keywords(user_message)

    if user_kw["matched_patterns"]:
        logger.info("User keyword extraction:")
        logger.info("  Matched patterns: %s", user_kw["matched_patterns"])
        logger.info("  Interest query words: %s", user_kw["interest_query_words"])
        logger.info("  Interest ll_keywords: %s", user_kw["interest_ll_keywords"])
        logger.info("  Audience query words: %s", user_kw["audience_query_words"])
        logger.info("  Audience ll_keywords: %s", user_kw["audience_ll_keywords"])
        logger.info("  Time hint: %s", user_kw["time_hint"])
    else:
        logger.info("User keyword extraction: no specific interests detected, using defaults")

    # Build time hint for route query
    time_word = user_kw["time_hint"] or "3小时"

    # Base query strings — will be extended with user keywords
    route_query_base = f"{time_word} 首次来馆 国宝 精华路线 推荐路线 必看文物 展览"
    event_query_base = f"{date} 今天 活动 讲解 导览 工作坊 体验 集章"
    notice_query_base = f"{date} 今天 关闭 维修 限流 排队 闭馆 注意事项"
    story_query_base = "咖啡 文创 冷知识 打卡 拍照 纪念品 趣味 小红书"

    # Base keywords
    route_hl = ["博物馆路线", "参观规划", "国宝精华"]
    route_ll = [time_word, "路线", "国宝", "精华", "首次", "必看", "文物", "展览", "推荐"]
    event_hl = ["博物馆活动", "今日讲解导览"]
    event_ll = ["活动", "讲解", "导览", "工作坊", "体验", "集章", "志愿者", date]
    notice_hl = ["运营公告", "注意事项"]
    notice_ll = ["关闭", "维修", "限流", "排队", "闭馆", "注意", "电梯", "门票", date]
    story_hl = ["趣味内容", "文创购物", "冷知识"]
    story_ll = ["咖啡", "文创", "冷知识", "打卡", "拍照", "纪念品", "小红书", "盲盒"]

    # Inject user interest keywords into route and story queries
    if user_kw["interest_query_words"]:
        extra = " ".join(user_kw["interest_query_words"])
        route_query_base += " " + extra
        story_query_base += " " + extra
    if user_kw["interest_ll_keywords"]:
        route_ll.extend(user_kw["interest_ll_keywords"])
        story_ll.extend(user_kw["interest_ll_keywords"])

    # Inject audience keywords into event queries
    if user_kw["audience_query_words"]:
        extra = " ".join(user_kw["audience_query_words"])
        event_query_base += " " + extra
    if user_kw["audience_ll_keywords"]:
        event_ll.extend(user_kw["audience_ll_keywords"])
        notice_ll.extend(user_kw["audience_ll_keywords"])

    # Deduplicate keywords while preserving order
    def _dedup(lst: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in lst:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    queries = {
        "route_data": (
            route_query_base,
            QueryParam(
                mode="mix", top_k=top_k, chunk_top_k=chunk_top_k,
                hl_keywords=_dedup(route_hl),
                ll_keywords=_dedup(route_ll),
            ),
        ),
        "event_data": (
            event_query_base,
            QueryParam(
                mode="mix", top_k=top_k, chunk_top_k=chunk_top_k,
                hl_keywords=_dedup(event_hl),
                ll_keywords=_dedup(event_ll),
            ),
        ),
        "notice_data": (
            notice_query_base,
            QueryParam(
                mode="mix", top_k=top_k, chunk_top_k=chunk_top_k,
                hl_keywords=_dedup(notice_hl),
                ll_keywords=_dedup(notice_ll),
            ),
        ),
        "story_data": (
            story_query_base,
            QueryParam(
                mode="mix", top_k=top_k, chunk_top_k=chunk_top_k,
                hl_keywords=_dedup(story_hl),
                ll_keywords=_dedup(story_ll),
            ),
        ),
    }

    # Log final queries
    logger.info("Final retrieval queries:")
    for key, (query, param) in queries.items():
        logger.info("  [%s] query: %s", key, query)
        logger.info("  [%s] hl_keywords: %s", key, param.hl_keywords)
        logger.info("  [%s] ll_keywords: %s", key, param.ll_keywords)

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
        formatted[key] = _format_retrieval_block(result, today=date)
        logger.info("  [%s] formatted text: %d chars", key, len(formatted[key]))
    logger.info("=" * 50)

    return formatted
