"""
Import dynamic museum events/notices/stories into LightRAG (PostgreSQL backend).

Reads museum_dynamic.json and museum.json (for ID→name lookups), then writes:
  - Nodes: Event, Notice, Story
  - Edges: HAPPENS_AT, AFFECTS, ABOUT
  - Chunks with temporal/spatial prefix for vector matching
  - Entity/Relation vectors, KV mappings, doc-level indexes

Usage:
    cd /home/all/cj/LightRAG
    python scripts/import_museum_dynamic.py
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=False)

from lightrag import LightRAG
from lightrag.utils import EmbeddingFunc, compute_mdhash_id, logger
from lightrag.constants import GRAPH_FIELD_SEP
from lightrag.llm.openai import openai_embed, openai_complete

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MUSEUM_JSON = os.path.join(PROJECT_ROOT, "museum.json")
DYNAMIC_JSON = os.path.join(PROJECT_ROOT, "museum_dynamic_0406.json")
WORKING_DIR = os.path.join(PROJECT_ROOT, "rag_storage")

DOC_ID_PREFIX = "museum_dynamic"

# Same scope tag as import_museum_kg.py. Numeric string matching bobo-agent's
# config/museums.yaml id. Default "15" = 中国国家博物馆 (2026-05 从 "2" 修正,
# 见 import_museum_kg.py 的注释)。
MUSEUM_ID = os.getenv("MUSEUM_ID", "15")


# ---------------------------------------------------------------------------
# Helpers: load static lookups
# ---------------------------------------------------------------------------
def load_static_lookups(museum: dict) -> dict:
    """Build ID→name mappings from static museum.json."""
    zone_id_to_name = {}
    for floor in museum["spatial_topology"]:
        for zone in floor["zones"]:
            zone_id_to_name[zone["zone_id"]] = zone["name"]

    art_id_to_name = {}
    for art in museum["artifacts"]:
        art_id_to_name[art["artifact_id"]] = art["name"]

    exh_id_to_name = {}
    for exh in museum.get("exhibitions", []):
        exh_id_to_name[exh["exhibition_id"]] = exh["name"]

    theme_id_to_name = {}
    for theme in museum.get("themes", []):
        theme_id_to_name[theme["theme_id"]] = theme["name"]

    return {
        "zone": zone_id_to_name,
        "artifact": art_id_to_name,
        "exhibition": exh_id_to_name,
        "theme": theme_id_to_name,
    }


# ---------------------------------------------------------------------------
# Resolve node type from JSON category
# ---------------------------------------------------------------------------
def resolve_node_type(item: dict) -> str:
    cat = item["category"]
    if cat == "activity":
        return "Event"
    if cat == "exhibition_update":
        sub = item.get("payload", {}).get("update_type", "")
        if sub == "opening":
            return "Event"
        return "Notice"
    if cat in ("ticket", "operation"):
        return "Notice"
    # content, merchandise, service
    return "Story"


# ---------------------------------------------------------------------------
# Format time for chunk prefix
# ---------------------------------------------------------------------------
def format_time_range(valid_from: str, valid_to: str) -> str:
    """Extract a human-readable time range for chunk prefix."""
    try:
        dt_from = datetime.fromisoformat(valid_from)
        dt_to = datetime.fromisoformat(valid_to)
        date_str = dt_from.strftime("%Y-%m-%d")
        time_from = dt_from.strftime("%H:%M")
        time_to = dt_to.strftime("%H:%M")
        if dt_from.date() == dt_to.date():
            return f"{date_str} {time_from}-{time_to}"
        date_to = dt_to.strftime("%Y-%m-%d")
        return f"{date_str}至{date_to}"
    except (ValueError, TypeError):
        return str(valid_from)


# ---------------------------------------------------------------------------
# Build graph data from dynamic items
# ---------------------------------------------------------------------------
def _node_data(entity_id: str, entity_type: str, description: str, **extra) -> dict:
    data = {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "description": description,
        "source_id": "",
        "file_path": "",
        "created_at": int(time.time()),
    }
    data.update(extra)
    return data


def _edge_data(description: str, keywords: str, weight: float = 1.0, **extra) -> dict:
    data = {
        "description": description,
        "keywords": keywords,
        "weight": weight,
        "source_id": "",
        "file_path": "",
        "created_at": int(time.time()),
    }
    data.update(extra)
    return data


def make_doc_id(date_tag: str, seq: int) -> str:
    """Generate per-item doc_id: museum_dynamic-{date}-{seq:03d}"""
    return f"{DOC_ID_PREFIX}-{date_tag}-{seq:03d}"


def build_dynamic_graph(items: list[dict], lookups: dict):
    """
    Returns:
        nodes: list of (node_id, node_data_dict)
        edges: list of (src_id, tgt_id, edge_data_dict)
        chunks: list of dict with keys: content, chunk_id, doc_id, valid_to
    """
    nodes = []
    edges = []
    chunks = []

    zone_map = lookups["zone"]
    art_map = lookups["artifact"]
    exh_map = lookups["exhibition"]
    theme_map = lookups["theme"]

    # Track per-date sequence numbers for doc_id generation
    date_seq: dict[str, int] = {}

    for item in items:
        if item.get("status") != "active":
            continue

        item_id = item["id"]
        node_type = resolve_node_type(item)
        title = item["title"]
        description = item["description"]
        priority = item.get("priority", 3)
        category = item["category"]
        valid_from = item.get("valid_from", "")
        valid_to = item.get("valid_to", "")
        tags = item.get("tags", [])
        target_audience = item.get("target_audience", [])

        # Resolve zone names for this item
        item_zone_names = [
            zone_map[zid] for zid in item.get("zone_ids", []) if zid in zone_map
        ]
        primary_zone = item_zone_names[0] if item_zone_names else ""

        # Determine file_path tag and per-item doc_id
        try:
            date_tag = datetime.fromisoformat(valid_from).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            date_tag = "undated"
        file_path_tag = f"museum:dynamic:{node_type.lower()}:{date_tag}"

        date_seq[date_tag] = date_seq.get(date_tag, 0) + 1
        item_doc_id = make_doc_id(date_tag, date_seq[date_tag])

        # --- Build chunk text ---
        time_str = format_time_range(valid_from, valid_to)

        # Handle recurrence — append schedule info
        recurrence = item.get("recurrence")
        if recurrence and recurrence.get("times"):
            time_str = f"{date_tag} {'/'.join(recurrence['times'])}"

        if node_type == "Event":
            activity_type = item.get("payload", {}).get("activity_type", "")
            prefix = f"【{time_str}｜{primary_zone}｜活动·{activity_type}】"
        elif node_type == "Notice":
            severity = item.get("payload", {}).get("severity", "info")
            severity_cn = {"info": "提示", "warning": "注意", "critical": "紧急"}.get(severity, severity)
            prefix = f"【{time_str}｜{primary_zone}｜{severity_cn}】"
        else:  # Story
            sub_type = (
                item.get("payload", {}).get("content_type")
                or item.get("payload", {}).get("merch_type")
                or item.get("payload", {}).get("service_type")
                or ""
            )
            prefix = f"【{time_str}｜{primary_zone}｜{sub_type}】"

        body = f"{title}：{description}"

        refs = []
        art_names_for_item = [
            art_map[aid] for aid in item.get("artifact_ids", []) if aid in art_map
        ]
        if art_names_for_item:
            refs.append(f"相关文物：{'、'.join(art_names_for_item)}")
        exh_names_for_item = [
            exh_map[eid] for eid in item.get("exhibition_ids", []) if eid in exh_map
        ]
        if exh_names_for_item:
            refs.append(f"相关展览：{'、'.join(exh_names_for_item)}")
        if tags:
            refs.append(f"标签：{'、'.join(tags)}")

        chunk_content = prefix + "\n" + body
        if refs:
            chunk_content += "\n" + "\n".join(refs)

        chunk_id = compute_mdhash_id(chunk_content, prefix="chunk-")

        chunks.append({
            "content": chunk_content,
            "chunk_id": chunk_id,
            "doc_id": item_doc_id,
            "valid_to": valid_to,
            "item_id": item_id,
            "node_type": node_type,
        })

        # --- Node ---
        node_desc = f"{prefix}\n{body}"
        nodes.append((
            title,
            _node_data(
                title, node_type, node_desc,
                source_id=chunk_id,
                file_path=file_path_tag,
                code=item_id,
                category=category,
                priority=priority,
                valid_from=valid_from,
                valid_to=valid_to,
                tags=",".join(tags),
                target_audience=",".join(target_audience) if target_audience else "all",
            ),
        ))

        edge_weight = priority / 5.0

        # --- HAPPENS_AT / AFFECTS edges → Zone ---
        # Edge descriptions use semantic context without entity names/times,
        # since src_id/tgt_id already carry the entity identity.
        category_cn = {"activity": "活动", "ticket": "票务", "operation": "运营",
                        "exhibition_update": "展览动态"}.get(category, category)
        for zone_name in item_zone_names:
            if node_type == "Notice":
                edge_desc = f"{category_cn}通知影响所在展区"
                edge_kw = "影响,通知,状态,公告"
            elif node_type == "Event":
                activity_type = item.get("payload", {}).get("activity_type", "")
                type_cn = {"guided_tour": "讲解导览", "workshop": "工作坊",
                           "lecture": "讲座", "performance": "演出",
                           "stamp_rally": "集章活动"}.get(activity_type, "活动")
                edge_desc = f"{type_cn}活动在展区举办"
                edge_kw = "活动,位置,发生地"
            else:
                edge_desc = f"{category_cn}资讯关联展区"
                edge_kw = "资讯,位置,可获取"
            edges.append((
                title, zone_name,
                _edge_data(edge_desc, edge_kw, weight=edge_weight,
                           source_id=chunk_id, file_path=file_path_tag),
            ))

        # --- ABOUT edges → Artifact ---
        for art_id in item.get("artifact_ids", []):
            art_name = art_map.get(art_id)
            if art_name:
                edges.append((
                    title, art_name,
                    _edge_data(
                        f"{category_cn}涉及相关文物",
                        "相关文物,涉及,关联",
                        weight=edge_weight,
                        source_id=chunk_id, file_path=file_path_tag,
                    ),
                ))

        # --- ABOUT edges → Exhibition ---
        for exh_id in item.get("exhibition_ids", []):
            exh_name = exh_map.get(exh_id)
            if exh_name:
                edges.append((
                    title, exh_name,
                    _edge_data(
                        f"{category_cn}关联所属展览",
                        "相关展览,配套,关联",
                        weight=edge_weight,
                        source_id=chunk_id, file_path=file_path_tag,
                    ),
                ))

        # --- ABOUT edges → Theme ---
        for theme_id in item.get("theme_ids", []):
            theme_name = theme_map.get(theme_id)
            if theme_name:
                edges.append((
                    title, theme_name,
                    _edge_data(
                        f"{category_cn}与相关主题关联",
                        "相关主题,主题关联",
                        weight=edge_weight,
                        source_id=chunk_id, file_path=file_path_tag,
                    ),
                ))

    return nodes, edges, chunks


# ---------------------------------------------------------------------------
# Import into LightRAG
# ---------------------------------------------------------------------------
async def do_import():
    with open(MUSEUM_JSON, "r", encoding="utf-8") as f:
        museum = json.load(f)
    with open(DYNAMIC_JSON, "r", encoding="utf-8") as f:
        dynamic = json.load(f)

    lookups = load_static_lookups(museum)
    items = dynamic.get("items", [])
    nodes, edges, chunks = build_dynamic_graph(items, lookups)
    logger.info(f"Built {len(nodes)} nodes, {len(edges)} edges, {len(chunks)} chunks from {len(items)} dynamic items")

    # --- Initialize LightRAG ---
    embedding_api_key = os.getenv("EMBEDDING_BINDING_API_KEY", "")
    embedding_host = os.getenv("EMBEDDING_BINDING_HOST", "https://api.openai.com/v1")
    embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")
    embedding_dim = int(os.getenv("EMBEDDING_DIM", "3072"))

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
        graph = rag.chunk_entity_relation_graph
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Upsert nodes
        logger.info("Upserting dynamic nodes ...")
        for node_id, node_data in nodes:
            await graph.upsert_node(node_id, node_data=node_data)
        logger.info(f"  {len(nodes)} nodes done")

        # 2. Upsert edges
        logger.info("Upserting dynamic edges ...")
        for src, tgt, edata in edges:
            await graph.upsert_edge(src, tgt, edge_data=edata)
        logger.info(f"  {len(edges)} edges done")

        # 3. Upsert chunks into vector + KV
        logger.info("Upserting chunks ...")
        chunk_kv = {}
        chunk_vdb = {}
        for c in chunks:
            cid = c["chunk_id"]
            doc_id = c["doc_id"]
            tok_count = len(rag.tokenizer.encode(c["content"]))
            chunk_kv[cid] = {
                "content": c["content"],
                "tokens": tok_count,
                "chunk_order_index": 0,
                "full_doc_id": doc_id,
                "file_path": "museum:dynamic",
            }
            chunk_vdb[cid] = {
                "content": c["content"],
                "tokens": tok_count,
                "chunk_order_index": 0,
                "full_doc_id": doc_id,
                "file_path": "museum:dynamic",
            }
        await asyncio.gather(
            rag.text_chunks.upsert(chunk_kv),
            rag.chunks_vdb.upsert(chunk_vdb),
        )
        logger.info(f"  {len(chunks)} chunks done")

        # 4. Upsert entity vectors
        logger.info("Upserting entity vectors ...")
        entity_vdb = {}
        for node_id, nd in nodes:
            ent_vec_id = compute_mdhash_id(node_id, prefix="ent-")
            desc = nd["description"]
            content = desc if desc.startswith(node_id) else node_id + "\n" + desc
            entity_vdb[ent_vec_id] = {
                "content": content,
                "entity_name": node_id,
                "source_id": nd["source_id"],
                "description": desc,
                "entity_type": nd["entity_type"],
                "file_path": nd.get("file_path", "museum:dynamic"),
            }
        await rag.entities_vdb.upsert(entity_vdb)
        logger.info(f"  {len(entity_vdb)} entity vectors done")

        # 5. Upsert relationship vectors
        logger.info("Upserting relationship vectors ...")
        rel_vdb = {}
        for src, tgt, ed in edges:
            rel_vec_id = compute_mdhash_id(src + tgt, prefix="rel-")
            rel_vdb[rel_vec_id] = {
                "src_id": src,
                "tgt_id": tgt,
                "source_id": ed.get("source_id", ""),
                "content": f"{ed['keywords']}\t{src}\n{tgt}\n{ed['description']}",
                "keywords": ed["keywords"],
                "description": ed["description"],
                "weight": ed["weight"],
                "file_path": ed.get("file_path", "museum:dynamic"),
            }
        await rag.relationships_vdb.upsert(rel_vdb)
        logger.info(f"  {len(rel_vdb)} relationship vectors done")

        # 6. Upsert entity_chunks and relation_chunks KV
        logger.info("Upserting KV mappings ...")
        ent_chunks_kv = {}
        for node_id, nd in nodes:
            sid = nd["source_id"]
            if sid:
                ent_chunks_kv[node_id] = {
                    "chunk_ids": sid.split(GRAPH_FIELD_SEP),
                    "count": len(sid.split(GRAPH_FIELD_SEP)),
                }
        rel_chunks_kv = {}
        for src, tgt, ed in edges:
            sid = ed.get("source_id", "")
            if sid:
                key = f"{src}{GRAPH_FIELD_SEP}{tgt}"
                rel_chunks_kv[key] = {
                    "chunk_ids": sid.split(GRAPH_FIELD_SEP),
                    "count": len(sid.split(GRAPH_FIELD_SEP)),
                }
        await asyncio.gather(
            rag.entity_chunks.upsert(ent_chunks_kv),
            rag.relation_chunks.upsert(rel_chunks_kv),
        )
        logger.info(f"  {len(ent_chunks_kv)} entity_chunks, {len(rel_chunks_kv)} relation_chunks done")

        # 7. Write per-item full_entities, full_relations, full_docs, doc_status
        logger.info("Writing per-item doc records ...")

        # Group nodes/edges by doc_id
        doc_entities: dict[str, list[str]] = {}
        doc_relations: dict[str, list[list[str]]] = {}
        doc_contents: dict[str, str] = {}
        for c in chunks:
            doc_id = c["doc_id"]
            doc_contents[doc_id] = c["content"]
            if doc_id not in doc_entities:
                doc_entities[doc_id] = []
                doc_relations[doc_id] = []

        # Each item has exactly 1 node — map node to its doc_id via chunk
        chunk_to_doc = {c["chunk_id"]: c["doc_id"] for c in chunks}
        for node_id, nd in nodes:
            d = chunk_to_doc.get(nd["source_id"])
            if d:
                doc_entities.setdefault(d, []).append(node_id)
        for src, tgt, ed in edges:
            d = chunk_to_doc.get(ed.get("source_id", ""))
            if d:
                doc_relations.setdefault(d, []).append([src, tgt])

        ent_upsert = {}
        rel_upsert = {}
        doc_upsert = {}
        status_upsert = {}
        for doc_id in doc_contents:
            ent_names = doc_entities.get(doc_id, [])
            rel_pairs = doc_relations.get(doc_id, [])
            ent_upsert[doc_id] = {"entity_names": ent_names, "count": len(ent_names)}
            rel_upsert[doc_id] = {"relation_pairs": rel_pairs, "count": len(rel_pairs)}
            doc_upsert[doc_id] = {"content": doc_contents[doc_id], "file_path": "museum_dynamic.json"}
            status_upsert[doc_id] = {
                "status": "PROCESSED",
                "content_summary": f"Dynamic item: {doc_id}",
                "content_length": len(doc_contents[doc_id]),
                "chunks_count": 1,
                "created_at": now_iso,
                "updated_at": now_iso,
                "file_path": "museum_dynamic.json",
            }

        await asyncio.gather(
            rag.full_entities.upsert(ent_upsert),
            rag.full_relations.upsert(rel_upsert),
            rag.full_docs.upsert(doc_upsert),
            rag.doc_status.upsert(status_upsert),
        )
        logger.info(f"  {len(doc_contents)} per-item doc records done")

        # ==============================================================
        # Tag dynamic rows with metadata.museum_id for scoped retrieval.
        # Dynamic chunks use file_path like "museum:dynamic:event:2026-04-20"
        # while entity/relation vdb rows use "museum:dynamic". We match both
        # by the "museum" prefix and only touch rows with empty metadata, so
        # re-runs are idempotent and don't overwrite existing tags (matters
        # for future multi-museum ingestion).
        # ==============================================================
        logger.info("Tagging dynamic rows with metadata.museum_id = %s ...", MUSEUM_ID)
        db = rag.chunks_vdb.db
        chunks_table = rag.chunks_vdb.table_name
        entities_table = rag.entities_vdb.table_name
        relations_table = rag.relationships_vdb.table_name
        workspace = rag.chunks_vdb.workspace
        museum_payload = json.dumps({"museum_id": MUSEUM_ID})

        tag_sql_tmpl = (
            "UPDATE {table} SET metadata = metadata || $2::jsonb "
            "WHERE workspace = $1 AND (metadata IS NULL OR NOT (metadata ? 'museum_id')) "
            "AND (file_path LIKE 'museum:%' OR file_path = 'museum_dynamic.json')"
        )
        tagged_counts: dict[str, int] = {}
        for table in (chunks_table, entities_table, relations_table):
            sql = tag_sql_tmpl.format(table=table)
            await db.execute(
                sql,
                {"workspace": workspace, "payload": museum_payload},
            )
            count_sql = (
                f"SELECT count(*) AS n FROM {table} "
                f"WHERE workspace = $1 AND metadata @> $2::jsonb"
            )
            res = await db.query(count_sql, params=[workspace, museum_payload])
            tagged_counts[table] = (res or {}).get("n", 0) if isinstance(res, dict) else 0
        logger.info("  metadata.museum_id tagged (cumulative per table): %s", tagged_counts)

        logger.info("Dynamic museum data import complete!")

    finally:
        await rag.finalize_storages()


if __name__ == "__main__":
    asyncio.run(do_import())
