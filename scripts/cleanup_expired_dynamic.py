#!/usr/bin/env python3
"""Clean up expired dynamic museum data from LightRAG.

Reads museum_dynamic.json, checks each item's valid_to against today,
and deletes expired items from the knowledge graph via adelete_by_doc_id().

Usage:
  python scripts/cleanup_expired_dynamic.py                    # dry-run (default)
  python scripts/cleanup_expired_dynamic.py --execute          # actually delete
  python scripts/cleanup_expired_dynamic.py --date 2026-04-07  # simulate a future date
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date, datetime

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=False)

from lightrag import LightRAG
from lightrag.llm.openai import openai_complete, openai_embed
from lightrag.utils import EmbeddingFunc

logger = logging.getLogger(__name__)

DYNAMIC_JSON = os.path.join(PROJECT_ROOT, "museum_dynamic.json")
WORKING_DIR = os.path.join(PROJECT_ROOT, "rag_storage")
DOC_ID_PREFIX = "museum_dynamic"


def resolve_node_type(item: dict) -> str:
    """Same logic as import script — map category to node type."""
    cat = item["category"]
    if cat == "activity":
        return "Event"
    if cat == "exhibition_update":
        sub = item.get("payload", {}).get("update_type", "")
        return "Event" if sub == "opening" else "Notice"
    if cat in ("ticket", "operation"):
        return "Notice"
    return "Story"


def check_expired(items: list[dict], today_str: str) -> tuple[list[dict], list[dict]]:
    """Split items into (expired, active) based on valid_to vs today.

    Returns:
        expired: items whose valid_to < today (should be deleted)
        active: items still valid
    """
    expired = []
    active = []

    for item in items:
        valid_to = item.get("valid_to", "")
        if not valid_to:
            active.append(item)
            continue

        try:
            vt_date = datetime.fromisoformat(valid_to).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            active.append(item)
            continue

        if vt_date < today_str:
            expired.append(item)
        else:
            active.append(item)

    return expired, active


def reconstruct_doc_id(item: dict, items: list[dict]) -> str:
    """Reconstruct the doc_id that was assigned during import.

    The import script assigns: museum_dynamic-{valid_from_date}-{seq}
    where seq is the order within items sharing the same date.
    """
    try:
        date_tag = datetime.fromisoformat(item.get("valid_from", "")).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_tag = "undated"

    # Count how many items with the same date appear before this one
    seq = 0
    for it in items:
        if it.get("status") != "active":
            continue
        try:
            it_date = datetime.fromisoformat(it.get("valid_from", "")).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            it_date = "undated"
        if it_date == date_tag:
            seq += 1
        if it["id"] == item["id"]:
            break

    return f"{DOC_ID_PREFIX}-{date_tag}-{seq:03d}"


async def run_cleanup(today_str: str, execute: bool = False):
    """Main cleanup logic."""
    with open(DYNAMIC_JSON, "r", encoding="utf-8") as f:
        dynamic = json.load(f)
    items = dynamic.get("items", [])

    expired, active = check_expired(items, today_str)

    logger.info("=" * 60)
    logger.info("Dynamic Data Cleanup Report")
    logger.info("=" * 60)
    logger.info("  Today: %s", today_str)
    logger.info("  Total items: %d", len(items))
    logger.info("  Expired: %d", len(expired))
    logger.info("  Active: %d", len(active))

    if not expired:
        logger.info("  Nothing to clean up.")
        return

    logger.info("")
    logger.info("Expired items:")
    for item in expired:
        node_type = resolve_node_type(item)
        doc_id = reconstruct_doc_id(item, items)
        logger.info("  [%s] %s (valid_to=%s) → doc_id=%s",
                     node_type, item["title"], item.get("valid_to", "?"), doc_id)

    if not execute:
        logger.info("")
        logger.info("DRY RUN — no changes made. Use --execute to delete.")
        return

    # Initialize LightRAG
    logger.info("")
    logger.info("Connecting to LightRAG...")

    embedding_api_key = os.getenv("EMBEDDING_BINDING_API_KEY", "")
    embedding_host = os.getenv("EMBEDDING_BINDING_HOST", "https://api.openai.com/v1")
    embedding_model = os.getenv("EMBEDDING_MODEL", "bge-m3")
    embedding_dim = int(os.getenv("EMBEDDING_DIM", "1024"))

    async def embedding_func(texts: list[str]) -> list:
        return await openai_embed.func(
            texts,
            model=embedding_model,
            base_url=embedding_host,
            api_key=embedding_api_key,
            embedding_dim=embedding_dim,
        )

    rag = LightRAG(
        working_dir=WORKING_DIR,
        llm_model_func=openai_complete,
        llm_model_name=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        embedding_func=EmbeddingFunc(
            embedding_dim=embedding_dim,
            max_token_size=int(os.getenv("EMBEDDING_TOKEN_LIMIT", "8192")),
            func=embedding_func,
            model_name=embedding_model,
        ),
        kv_storage=os.getenv("LIGHTRAG_KV_STORAGE", "PGKVStorage"),
        graph_storage=os.getenv("LIGHTRAG_GRAPH_STORAGE", "PGGraphStorage"),
        vector_storage=os.getenv("LIGHTRAG_VECTOR_STORAGE", "PGVectorStorage"),
        doc_status_storage=os.getenv("LIGHTRAG_DOC_STATUS_STORAGE", "PGDocStatusStorage"),
    )
    await rag.initialize_storages()

    try:
        deleted = 0
        failed = 0
        for item in expired:
            doc_id = reconstruct_doc_id(item, items)
            try:
                result = await rag.adelete_by_doc_id(doc_id)
                if result.status == "success":
                    logger.info("  Deleted: %s (%s)", doc_id, item["title"])
                    deleted += 1
                else:
                    logger.warning("  Not found: %s (%s) — %s", doc_id, item["title"], result.message)
                    failed += 1
            except Exception:
                logger.exception("  Error deleting %s", doc_id)
                failed += 1

        logger.info("")
        logger.info("Cleanup complete: %d deleted, %d failed", deleted, failed)

    finally:
        await rag.finalize_storages()


def main():
    parser = argparse.ArgumentParser(description="Clean up expired dynamic museum data")
    parser.add_argument("--date", type=str, default=date.today().isoformat(),
                        help="Reference date for expiry check (default: today)")
    parser.add_argument("--execute", action="store_true",
                        help="Actually delete (default: dry-run)")
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    asyncio.run(run_cleanup(args.date, args.execute))


if __name__ == "__main__":
    main()
