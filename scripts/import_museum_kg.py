"""
Import structured museum knowledge graph data into LightRAG (PostgreSQL backend).

Reads museum.json and builds:
  - Nodes: Museum, Floor, Exhibition_Hall, Facility, Artifact, Concept,
           Exhibition, Theme, Route
  - Edges: BELONGS_TO, LOCATED_ON, HAS_CATEGORY, DISPLAYED_IN, ADJACENT_TO,
           ACCESSIBLE_BY_STAIRS, HOSTED_IN, INCLUDES, HAS_THEME, RELATED_TO,
           ROUTE_STOP
  - Chunks: Artifact / Exhibition / Theme / Route descriptions -> vector embeddings
  - Entity/Relation vectors for semantic retrieval
  - Full entity/relation indexes for document-level management

Usage:
    cd /home/all/cj/LightRAG
    python scripts/import_museum_kg.py
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

# Ensure project root is on sys.path
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
WORKING_DIR = os.path.join(PROJECT_ROOT, "rag_storage")
FILE_PATH_TAG = "museum.json"  # stored as file_path in graph properties

# A document-level source_id for the entire import batch
DOC_ID = "museum_import"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _node_data(entity_id: str, entity_type: str, description: str, **extra) -> dict:
    """Build a node property dict compatible with PGGraphStorage.upsert_node."""
    data = {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "description": description,
        "source_id": "",
        "file_path": FILE_PATH_TAG,
        "created_at": int(time.time()),
    }
    data.update(extra)
    return data


def _edge_data(description: str, keywords: str, weight: float = 1.0, **extra) -> dict:
    """Build an edge property dict compatible with PGGraphStorage.upsert_edge."""
    data = {
        "description": description,
        "keywords": keywords,
        "weight": weight,
        "source_id": "",
        "file_path": FILE_PATH_TAG,
        "created_at": int(time.time()),
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# Build entities & relationships from museum.json
# ---------------------------------------------------------------------------
def build_graph_data(museum: dict):
    """
    Returns:
        nodes: list of (node_id, node_data_dict)
        edges: list of (src_id, tgt_id, edge_data_dict)
        chunks: list of dict with keys: content, chunk_id, source_tag
    """
    nodes = []
    edges = []
    chunks = []
    concept_set = set()

    # --- Lookup tables ---
    zone_id_to_name: dict[str, str] = {}
    for floor in museum["spatial_topology"]:
        for zone in floor["zones"]:
            zone_id_to_name[zone["zone_id"]] = zone["name"]

    art_id_to_name: dict[str, str] = {}
    for art in museum["artifacts"]:
        art_id_to_name[art["artifact_id"]] = art["name"]

    info = museum["museum"]
    museum_name = info["name"]

    # ===================================================================
    # 1. Museum node
    # ===================================================================
    nodes.append((
        museum_name,
        _node_data(
            museum_name, "Museum",
            f"{museum_name}，位于{info['city']}{info['address']}",
            code=info["museum_id"],
            city=info["city"],
            address=info["address"],
        ),
    ))

    # ===================================================================
    # 2. Floors, Zones
    # ===================================================================
    for floor in museum["spatial_topology"]:
        floor_name = floor["floor_name"]

        nodes.append((
            floor_name,
            _node_data(floor_name, "Floor", f"{museum_name} {floor_name}",
                       code=floor["floor_id"]),
        ))
        edges.append((
            floor_name, museum_name,
            _edge_data(f"{floor_name}属于{museum_name}", "从属,行政,楼层"),
        ))

        for zone in floor["zones"]:
            zone_name = zone["name"]
            zone_type = zone["type"]

            if zone_type == "Exhibition_Hall":
                desc = f"{zone_name}，当前展览：{zone.get('current_exhibition', '无')}"
                extra = {"current_exhibition": zone.get("current_exhibition", "")}
            else:
                desc = f"{zone_name}（{zone.get('facility_type', '')}）"
                extra = {"facility_type": zone.get("facility_type", "")}

            nodes.append((
                zone_name,
                _node_data(zone_name, zone_type, desc, code=zone["zone_id"], **extra),
            ))
            edges.append((
                zone_name, floor_name,
                _edge_data(f"{zone_name}位于{floor_name}", "位置,楼层"),
            ))

    # ===================================================================
    # 3. Artifacts + Concept nodes
    # ===================================================================
    art_chunk_map: dict[str, str] = {}
    for art in museum["artifacts"]:
        art_name = art["name"]
        chunk_content = f"{art_name}：{art['description']}"
        chunk_id = compute_mdhash_id(chunk_content, prefix="chunk-")
        art_chunk_map[art_name] = chunk_id
        chunks.append({
            "content": chunk_content,
            "chunk_id": chunk_id,
            "source_tag": "artifact",
        })

    for art in museum["artifacts"]:
        art_name = art["name"]
        category = art["category"]
        zone_name = zone_id_to_name[art["located_in_zone_id"]]
        chunk_id = art_chunk_map[art_name]

        nodes.append((
            art_name,
            _node_data(
                art_name, "Artifact", art["description"],
                source_id=chunk_id,
                code=art["artifact_id"],
                category=category,
                image_url=art.get("image_url", ""),
            ),
        ))
        edges.append((
            art_name, zone_name,
            _edge_data(f"{art_name}陈列于{zone_name}", "陈列,展出,位置",
                       source_id=chunk_id),
        ))

        concept_id = f"文物分类_{category}"
        if concept_id not in concept_set:
            concept_set.add(concept_id)
            concept_chunk_ids: list[str] = [
                art_chunk_map[a["name"]]
                for a in museum["artifacts"]
                if a["category"] == category
            ]
            nodes.append((
                concept_id,
                _node_data(concept_id, "Concept", f"文物分类：{category}",
                           source_id=GRAPH_FIELD_SEP.join(concept_chunk_ids)),
            ))
        edges.append((
            art_name, concept_id,
            _edge_data(f"{art_name}属于{category}类别", "分类,类别",
                       source_id=chunk_id),
        ))

    # ===================================================================
    # 4. Exhibitions (promoted from zone property to first-class node)
    # ===================================================================
    for exh in museum.get("exhibitions", []):
        exh_name = exh["name"]
        zone_name = zone_id_to_name[exh["zone_id"]]

        # Build rich chunk text
        chunk_parts = [f"展览「{exh_name}」：{exh['description']}"]
        if exh.get("tips"):
            chunk_parts.append(f"参观提示：{exh['tips']}")
        if exh.get("type") == "temporary":
            chunk_parts.append(f"展期：{exh.get('valid_from', '')} 至 {exh.get('valid_to', '')}")
        chunk_content = "\n".join(chunk_parts)
        chunk_id = compute_mdhash_id(chunk_content, prefix="chunk-")
        chunks.append({
            "content": chunk_content,
            "chunk_id": chunk_id,
            "source_tag": "exhibition",
        })

        extra_props = {"code": exh["exhibition_id"], "exhibition_type": exh.get("type", "permanent")}
        if exh.get("valid_from"):
            extra_props["valid_from"] = exh["valid_from"]
        if exh.get("valid_to"):
            extra_props["valid_to"] = exh["valid_to"]

        nodes.append((
            exh_name,
            _node_data(exh_name, "Exhibition", exh["description"],
                       source_id=chunk_id, **extra_props),
        ))

        # HOSTED_IN: exhibition -> zone
        edges.append((
            exh_name, zone_name,
            _edge_data(f"展览「{exh_name}」在{zone_name}举办", "展览,展厅,位置",
                       source_id=chunk_id),
        ))

        # INCLUDES: exhibition -> artifact
        for art_id in exh.get("artifact_ids", []):
            art_name = art_id_to_name.get(art_id)
            if art_name:
                edges.append((
                    exh_name, art_name,
                    _edge_data(f"展览「{exh_name}」包含{art_name}", "展览,展品,包含",
                               source_id=chunk_id),
                ))

    # ===================================================================
    # 5. Themes (cross-cutting thematic threads)
    # ===================================================================
    for theme in museum.get("themes", []):
        theme_name = theme["name"]
        chunk_content = f"主题「{theme_name}」：{theme['description']}"
        chunk_id = compute_mdhash_id(chunk_content, prefix="chunk-")
        chunks.append({
            "content": chunk_content,
            "chunk_id": chunk_id,
            "source_tag": "theme",
        })

        # Collect chunk_ids of related artifacts for source_id
        related_chunk_ids = [
            art_chunk_map[art_id_to_name[aid]]
            for aid in theme.get("artifact_ids", [])
            if aid in art_id_to_name and art_id_to_name[aid] in art_chunk_map
        ]
        all_source_ids = [chunk_id] + related_chunk_ids

        nodes.append((
            theme_name,
            _node_data(theme_name, "Theme", theme["description"],
                       source_id=GRAPH_FIELD_SEP.join(all_source_ids),
                       code=theme["theme_id"]),
        ))

        # HAS_THEME: artifact -> theme
        for art_id in theme.get("artifact_ids", []):
            art_name = art_id_to_name.get(art_id)
            if art_name:
                art_cid = art_chunk_map.get(art_name, "")
                edges.append((
                    art_name, theme_name,
                    _edge_data(f"{art_name}属于主题「{theme_name}」", "主题,关联,叙事线索",
                               source_id=art_cid),
                ))

        # Theme -> span zones (for spatial awareness)
        for zone_code in theme.get("span_zones", []):
            z_name = zone_id_to_name.get(zone_code)
            if z_name:
                edges.append((
                    theme_name, z_name,
                    _edge_data(f"主题「{theme_name}」的文物分布在{z_name}", "主题,分布,区域",
                               source_id=chunk_id),
                ))

    # ===================================================================
    # 6. Routes (recommended visit routes)
    # ===================================================================
    for route in museum.get("routes", []):
        route_name = route["name"]

        # Build a rich route description chunk
        parts = [
            f"推荐路线「{route_name}」：{route['description']}",
            f"预计时长：{route['duration_minutes']}分钟，适合：{route['target_audience']}",
            "路线步骤：",
        ]
        for i, stop in enumerate(route["stops"], 1):
            z_name = zone_id_to_name.get(stop["zone_id"], stop["zone_id"])
            highlight_names = [
                art_id_to_name.get(aid, aid)
                for aid in stop.get("highlight_artifacts", [])
            ]
            step_text = f"  第{i}站：{z_name}（约{stop['duration_min']}分钟）"
            if highlight_names:
                step_text += f"——重点看：{'、'.join(highlight_names)}"
            if stop.get("note"):
                step_text += f"。{stop['note']}"
            parts.append(step_text)

        chunk_content = "\n".join(parts)
        chunk_id = compute_mdhash_id(chunk_content, prefix="chunk-")
        chunks.append({
            "content": chunk_content,
            "chunk_id": chunk_id,
            "source_tag": "route",
        })

        nodes.append((
            route_name,
            _node_data(route_name, "Route", route["description"],
                       source_id=chunk_id,
                       code=route["route_id"],
                       duration_minutes=route["duration_minutes"],
                       target_audience=route["target_audience"]),
        ))

        # ROUTE_STOP: route -> zone/artifact (with order)
        for i, stop in enumerate(route["stops"], 1):
            z_name = zone_id_to_name.get(stop["zone_id"], stop["zone_id"])
            edges.append((
                route_name, z_name,
                _edge_data(
                    f"路线「{route_name}」第{i}站：{z_name}（{stop['duration_min']}分钟）",
                    "路线,站点,动线",
                    weight=float(i),  # use stop order as weight
                    source_id=chunk_id,
                    stop_order=i,
                    duration_min=stop["duration_min"],
                ),
            ))
            for art_id in stop.get("highlight_artifacts", []):
                art_name = art_id_to_name.get(art_id)
                if art_name:
                    edges.append((
                        route_name, art_name,
                        _edge_data(
                            f"路线「{route_name}」第{i}站重点推荐{art_name}",
                            "路线,推荐,文物",
                            source_id=chunk_id,
                            stop_order=i,
                        ),
                    ))

    # ===================================================================
    # 7. Artifact relations (artifact <-> artifact)
    # ===================================================================
    for rel in museum.get("artifact_relations", []):
        src_name = art_id_to_name.get(rel["source_artifact_id"])
        tgt_name = art_id_to_name.get(rel["target_artifact_id"])
        if not src_name or not tgt_name:
            continue

        # Create a chunk for the relation description
        chunk_content = f"{src_name}与{tgt_name}的关联：{rel['description']}"
        chunk_id = compute_mdhash_id(chunk_content, prefix="chunk-")
        chunks.append({
            "content": chunk_content,
            "chunk_id": chunk_id,
            "source_tag": "artifact_relation",
        })

        edges.append((
            src_name, tgt_name,
            _edge_data(rel["description"], "关联,对比,历史,文物",
                       source_id=chunk_id),
        ))

    # ===================================================================
    # 8. Spatial topology edges (horizontal + vertical)
    # ===================================================================
    horizontal = [
        ("CN_NMC_ZONE_B1_NORTH", "CN_NMC_ZONE_B1_SOUTH", 5),
        ("CN_NMC_ZONE_B1_SOUTH", "CN_NMC_FAC_CAFETERIA", 2),
        ("CN_NMC_ZONE_F1_NORTH", "CN_NMC_ZONE_F1_SOUTH", 4),
        ("CN_NMC_FAC_VISITOR_CENTER", "CN_NMC_ZONE_F1_NORTH", 1),
        ("CN_NMC_FAC_LUGGAGE", "CN_NMC_FAC_VISITOR_CENTER", 1),
        ("CN_NMC_ZONE_F2_NORTH", "CN_NMC_ZONE_F2_SOUTH", 3),
        ("CN_NMC_ZONE_F2_SOUTH", "CN_NMC_FAC_GIFT_SHOP", 2),
        ("CN_NMC_ZONE_F3_NORTH", "CN_NMC_ZONE_F3_SOUTH", 3),
        ("CN_NMC_ZONE_F3_SOUTH", "CN_NMC_FAC_CAFE", 2),
    ]
    for src_code, tgt_code, w in horizontal:
        src = zone_id_to_name[src_code]
        tgt = zone_id_to_name[tgt_code]
        edges.append((
            src, tgt,
            _edge_data(
                f"{src}与{tgt}相邻，步行约{w}分钟",
                "相邻,通行,水平,动线",
                weight=float(w),
                connectivity_type="horizontal",
            ),
        ))

    vertical = [
        ("CN_NMC_ZONE_B1_NORTH", "CN_NMC_ZONE_F1_NORTH", 3),
        ("CN_NMC_ZONE_F1_NORTH", "CN_NMC_ZONE_F2_NORTH", 3),
        ("CN_NMC_ZONE_F2_NORTH", "CN_NMC_ZONE_F3_NORTH", 3),
        ("CN_NMC_ZONE_B1_SOUTH", "CN_NMC_ZONE_F1_SOUTH", 3),
        ("CN_NMC_ZONE_F1_SOUTH", "CN_NMC_ZONE_F2_SOUTH", 3),
        ("CN_NMC_ZONE_F2_SOUTH", "CN_NMC_ZONE_F3_SOUTH", 3),
    ]
    for src_code, tgt_code, w in vertical:
        src = zone_id_to_name[src_code]
        tgt = zone_id_to_name[tgt_code]
        edges.append((
            src, tgt,
            _edge_data(
                f"{src}与{tgt}通过楼梯/电梯连通，约{w}分钟",
                "楼梯,电梯,垂直,连通",
                weight=float(w),
                connectivity_type="vertical",
            ),
        ))

    return nodes, edges, chunks


# ---------------------------------------------------------------------------
# Import into LightRAG
# ---------------------------------------------------------------------------
async def do_import():
    with open(MUSEUM_JSON, "r", encoding="utf-8") as f:
        museum = json.load(f)

    nodes, edges, chunks = build_graph_data(museum)
    logger.info(f"Built {len(nodes)} nodes, {len(edges)} edges, {len(chunks)} chunks")

    # --- Initialize LightRAG with PG backend (reads .env automatically) ---
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

        # ==============================================================
        # 1. Upsert all nodes into graph storage
        # ==============================================================
        logger.info("Upserting nodes into graph storage ...")
        for node_id, node_data in nodes:
            await graph.upsert_node(node_id, node_data=node_data)
        logger.info(f"  {len(nodes)} nodes done")

        # ==============================================================
        # 2. Upsert all edges into graph storage
        # ==============================================================
        logger.info("Upserting edges into graph storage ...")
        for src, tgt, edata in edges:
            await graph.upsert_edge(src, tgt, edge_data=edata)
        logger.info(f"  {len(edges)} edges done")

        # ==============================================================
        # 3. Upsert chunks into vector + KV storage
        # ==============================================================
        logger.info("Upserting chunks into vector + KV storage ...")
        chunk_kv_data = {}
        chunk_vdb_data = {}
        for c in chunks:
            cid = c["chunk_id"]
            chunk_kv_data[cid] = {
                "content": c["content"],
                "tokens": len(rag.tokenizer.encode(c["content"])),
                "chunk_order_index": 0,
                "full_doc_id": DOC_ID,
                "file_path": FILE_PATH_TAG,
            }
            chunk_vdb_data[cid] = {
                "content": c["content"],
                "tokens": len(rag.tokenizer.encode(c["content"])),
                "chunk_order_index": 0,
                "full_doc_id": DOC_ID,
                "file_path": FILE_PATH_TAG,
            }
        await asyncio.gather(
            rag.text_chunks.upsert(chunk_kv_data),
            rag.chunks_vdb.upsert(chunk_vdb_data),
        )
        logger.info(f"  {len(chunks)} chunks done")

        # ==============================================================
        # 4. Upsert entities into vector storage
        # ==============================================================
        logger.info("Upserting entity vectors ...")
        entity_vdb_data = {}
        for node_id, node_data in nodes:
            ent_vec_id = compute_mdhash_id(node_id, prefix="ent-")
            desc = node_data["description"]
            content = desc if desc.startswith(node_id) else node_id + "\n" + desc
            entity_vdb_data[ent_vec_id] = {
                "content": content,
                "entity_name": node_id,
                "source_id": node_data["source_id"],
                "description": node_data["description"],
                "entity_type": node_data["entity_type"],
                "file_path": node_data.get("file_path", FILE_PATH_TAG),
            }
        await rag.entities_vdb.upsert(entity_vdb_data)
        logger.info(f"  {len(entity_vdb_data)} entity vectors done")

        # ==============================================================
        # 5. Upsert relationships into vector storage
        # ==============================================================
        logger.info("Upserting relationship vectors ...")
        rel_vdb_data = {}
        for src, tgt, edata in edges:
            rel_vec_id = compute_mdhash_id(src + tgt, prefix="rel-")
            rel_vdb_data[rel_vec_id] = {
                "src_id": src,
                "tgt_id": tgt,
                "source_id": edata.get("source_id", ""),
                "content": f"{edata['keywords']}\t{src}\n{tgt}\n{edata['description']}",
                "keywords": edata["keywords"],
                "description": edata["description"],
                "weight": edata["weight"],
                "file_path": edata.get("file_path", FILE_PATH_TAG),
            }
        await rag.relationships_vdb.upsert(rel_vdb_data)
        logger.info(f"  {len(rel_vdb_data)} relationship vectors done")

        # ==============================================================
        # 6. Upsert entity_chunks and relation_chunks KV mappings
        # ==============================================================
        logger.info("Upserting entity_chunks and relation_chunks KV mappings ...")
        entity_chunks_kv = {}
        for node_id, node_data in nodes:
            sid = node_data["source_id"]
            if sid:
                chunk_ids = sid.split(GRAPH_FIELD_SEP)
                entity_chunks_kv[node_id] = {
                    "chunk_ids": chunk_ids,
                    "count": len(chunk_ids),
                }
        relation_chunks_kv = {}
        for src, tgt, edata in edges:
            sid = edata.get("source_id", "")
            if sid:
                chunk_ids = sid.split(GRAPH_FIELD_SEP)
                key = f"{src}{GRAPH_FIELD_SEP}{tgt}"
                relation_chunks_kv[key] = {
                    "chunk_ids": chunk_ids,
                    "count": len(chunk_ids),
                }
        await asyncio.gather(
            rag.entity_chunks.upsert(entity_chunks_kv),
            rag.relation_chunks.upsert(relation_chunks_kv),
        )
        logger.info(
            f"  {len(entity_chunks_kv)} entity_chunks, "
            f"{len(relation_chunks_kv)} relation_chunks done"
        )

        # ==============================================================
        # 7. Write full_entities & full_relations (document-level index)
        # ==============================================================
        logger.info("Writing full_entities and full_relations ...")
        all_entity_names = [node_id for node_id, _ in nodes]
        all_relation_pairs = [[src, tgt] for src, tgt, _ in edges]

        await asyncio.gather(
            rag.full_entities.upsert({
                DOC_ID: {
                    "entity_names": all_entity_names,
                    "count": len(all_entity_names),
                }
            }),
            rag.full_relations.upsert({
                DOC_ID: {
                    "relation_pairs": all_relation_pairs,
                    "count": len(all_relation_pairs),
                }
            }),
        )
        logger.info(
            f"  {len(all_entity_names)} entities, "
            f"{len(all_relation_pairs)} relations indexed under doc '{DOC_ID}'"
        )

        # ==============================================================
        # 8. Write doc_full & doc_status
        # ==============================================================
        logger.info("Writing doc_full and doc_status ...")
        with open(MUSEUM_JSON, "r", encoding="utf-8") as f:
            museum_raw = f.read()

        await asyncio.gather(
            rag.full_docs.upsert({
                DOC_ID: {
                    "content": museum_raw,
                    "file_path": FILE_PATH_TAG,
                }
            }),
            rag.doc_status.upsert({
                DOC_ID: {
                    "status": "PROCESSED",
                    "content_summary": (
                        f"Museum knowledge graph import: "
                        f"{len(nodes)} nodes, {len(edges)} edges, {len(chunks)} chunks"
                    ),
                    "content_length": len(museum_raw),
                    "chunks_count": len(chunks),
                    "created_at": now_iso,
                    "updated_at": now_iso,
                    "file_path": FILE_PATH_TAG,
                }
            }),
        )
        logger.info("  doc_full and doc_status done")

        logger.info("Museum knowledge graph import complete!")

    finally:
        await rag.finalize_storages()


if __name__ == "__main__":
    asyncio.run(do_import())
