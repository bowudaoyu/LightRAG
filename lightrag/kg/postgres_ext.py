"""Extended PG graph operations — combined queries to reduce round-trips.

New file (not modifying upstream postgres_impl.py) to minimize merge conflicts.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


async def pg_get_node_data_combined(
    graph_storage,
    node_ids: list[str],
    vdb_results: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Replacement for _get_node_data's 7-query chain — does it in 1 SQL.

    Combines: get_nodes_batch + node_degrees_batch +
              get_nodes_edges_batch + get_edges_batch + edge_degrees_batch

    Args:
        graph_storage: PGGraphStorage instance (uses ._query and .graph_name)
        node_ids: entity names from vector search
        vdb_results: original VDB results (for created_at)

    Returns:
        (node_datas, edge_datas) matching _get_node_data's return format.
    """
    if not node_ids:
        return [], []

    gn = graph_storage.graph_name
    t0 = time.time()

    # Single SQL: nodes + degrees + all connected edges with properties
    query = f"""
    WITH input(v, ord) AS (
        SELECT v, ord
        FROM unnest($1::text[]) WITH ORDINALITY AS t(v, ord)
    ),
    ids(node_id, ord) AS (
        SELECT (to_json(v)::text)::agtype AS node_id, ord FROM input
    ),
    -- Matched nodes (vertex id + properties)
    vids AS (
        SELECT b.id AS vid, i.node_id, i.ord, b.properties
        FROM {gn}.base AS b
        JOIN ids i ON ag_catalog.agtype_access_operator(
            VARIADIC ARRAY[b.properties, '"entity_id"'::agtype]
        ) = i.node_id
    ),
    -- Node degrees (out + in)
    deg_out AS (
        SELECT d.start_id AS vid, COUNT(*)::bigint AS cnt
        FROM {gn}."DIRECTED" d
        JOIN vids v ON v.vid = d.start_id
        GROUP BY d.start_id
    ),
    deg_in AS (
        SELECT d.end_id AS vid, COUNT(*)::bigint AS cnt
        FROM {gn}."DIRECTED" d
        JOIN vids v ON v.vid = d.end_id
        GROUP BY d.end_id
    ),
    -- All edges touching any matched node
    all_edges AS (
        SELECT DISTINCT d.id, d.start_id, d.end_id, d.properties AS eprops
        FROM {gn}."DIRECTED" d
        WHERE d.start_id IN (SELECT vid FROM vids)
           OR d.end_id IN (SELECT vid FROM vids)
    ),
    -- Resolve edge endpoint entity_ids + edge properties
    edges_resolved AS (
        SELECT
            ag_catalog.agtype_access_operator(
                VARIADIC ARRAY[sb.properties, '"entity_id"'::agtype]
            )::text AS src_eid,
            ag_catalog.agtype_access_operator(
                VARIADIC ARRAY[tb.properties, '"entity_id"'::agtype]
            )::text AS tgt_eid,
            ae.eprops::text AS eprops_text
        FROM all_edges ae
        JOIN {gn}.base sb ON sb.id = ae.start_id
        JOIN {gn}.base tb ON tb.id = ae.end_id
    ),
    -- Degrees for ALL edge-endpoint nodes (needed for edge ranking)
    endpoint_vids AS (
        SELECT DISTINCT vid FROM (
            SELECT start_id AS vid FROM all_edges
            UNION
            SELECT end_id AS vid FROM all_edges
        ) t
    ),
    ep_deg AS (
        SELECT ep.vid,
               COALESCE(dout.cnt, 0) + COALESCE(din.cnt, 0) AS degree,
               ag_catalog.agtype_access_operator(
                   VARIADIC ARRAY[b.properties, '"entity_id"'::agtype]
               )::text AS eid
        FROM endpoint_vids ep
        JOIN {gn}.base b ON b.id = ep.vid
        LEFT JOIN (
            SELECT start_id, COUNT(*)::bigint AS cnt
            FROM {gn}."DIRECTED" GROUP BY start_id
        ) dout ON dout.start_id = ep.vid
        LEFT JOIN (
            SELECT end_id, COUNT(*)::bigint AS cnt
            FROM {gn}."DIRECTED" GROUP BY end_id
        ) din ON din.end_id = ep.vid
    )
    -- === Result set: Nodes (rtype=N) then Edges (rtype=E) ===
    SELECT * FROM (
        SELECT 'N'::text AS rtype,
               v.node_id::text AS col1,
               v.properties::text AS col2,
               (COALESCE(o.cnt, 0) + COALESCE(n.cnt, 0))::bigint AS col3,
               NULL::text AS col4
        FROM vids v
        LEFT JOIN deg_out o ON o.vid = v.vid
        LEFT JOIN deg_in  n ON n.vid = v.vid
        ORDER BY v.ord
    ) AS node_rows

    UNION ALL

    SELECT 'E'::text AS rtype,
           er.src_eid AS col1,
           er.eprops_text AS col2,
           (COALESCE(sd.degree, 0) + COALESCE(td.degree, 0))::bigint AS col3,
           er.tgt_eid AS col4
    FROM edges_resolved er
    LEFT JOIN ep_deg sd ON sd.eid = er.src_eid
    LEFT JOIN ep_deg td ON td.eid = er.tgt_eid;
    """

    results = await graph_storage._query(query, params={"ids": node_ids})
    elapsed = time.time() - t0

    # Build VDB lookup for created_at
    vdb_lookup = {r["entity_name"]: r for r in vdb_results}

    # Parse nodes
    node_datas: list[dict[str, Any]] = []
    for row in results:
        if row.get("rtype") != "N":
            continue
        eid = _strip_agtype_quotes(row["col1"])
        props = row["col2"]
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except json.JSONDecodeError:
                props = {}
        degree = int(row["col3"] or 0)
        vdb_r = vdb_lookup.get(eid, {})
        node_datas.append({
            **props,
            "entity_name": eid,
            "rank": degree,
            "created_at": vdb_r.get("created_at"),
        })

    # Parse edges (deduplicate by sorted pair)
    edge_datas: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()
    for row in results:
        if row.get("rtype") != "E":
            continue
        src = _strip_agtype_quotes(row["col1"])
        tgt = _strip_agtype_quotes(row["col4"])
        edge_key = tuple(sorted((src, tgt)))
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)

        eprops = row["col2"]
        if isinstance(eprops, str):
            try:
                eprops = json.loads(eprops)
            except json.JSONDecodeError:
                eprops = {}
        eprops = eprops or {}
        if "weight" not in eprops:
            eprops["weight"] = 1.0

        edge_datas.append({
            "src_tgt": (src, tgt),
            "rank": int(row["col3"] or 0),
            **eprops,
        })

    edge_datas.sort(key=lambda x: (x["rank"], x.get("weight", 0)), reverse=True)

    logger.info(
        "[Perf] pg_get_node_data_combined: %.3fs → %d nodes, %d edges (1 SQL)",
        elapsed, len(node_datas), len(edge_datas),
    )
    return node_datas, edge_datas


async def pg_get_edge_data_combined(
    graph_storage,
    edge_pairs: list[dict[str, str]],
    vdb_results: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Replacement for _get_edge_data's 4-query chain — does it in 1 SQL.

    Combines: get_edges_batch (fwd+bwd) + _find_most_related_entities_from_relationships
              (get_nodes_batch)

    Uses direct AGE table access (same as pg_get_node_data_combined), avoiding
    cypher() function calls that have agtype casting issues with SQL CTEs.

    Returns:
        (edge_datas, entity_datas) matching _get_edge_data's return format.
    """
    if not edge_pairs:
        return [], []

    gn = graph_storage.graph_name
    t0 = time.time()

    # Two parallel arrays for PG UNNEST
    src_ids = [p["src"] for p in edge_pairs]
    tgt_ids = [p["tgt"] for p in edge_pairs]

    query = f"""
    WITH input_pairs AS (
        SELECT s AS src_eid, t AS tgt_eid, ord
        FROM unnest($1::text[], $2::text[]) WITH ORDINALITY AS t(s, t, ord)
    ),
    -- Resolve src entity_id → vertex id
    src_vids AS (
        SELECT ip.src_eid, ip.tgt_eid, ip.ord, b.id AS src_vid
        FROM input_pairs ip
        JOIN {gn}.base b ON ag_catalog.agtype_access_operator(
            VARIADIC ARRAY[b.properties, '"entity_id"'::agtype]
        ) = (to_json(ip.src_eid)::text)::agtype
    ),
    -- Resolve tgt entity_id → vertex id
    pair_vids AS (
        SELECT sv.src_eid, sv.tgt_eid, sv.ord, sv.src_vid, b.id AS tgt_vid
        FROM src_vids sv
        JOIN {gn}.base b ON ag_catalog.agtype_access_operator(
            VARIADIC ARRAY[b.properties, '"entity_id"'::agtype]
        ) = (to_json(sv.tgt_eid)::text)::agtype
    ),
    -- Find edges (both directions: src→tgt OR tgt→src)
    matched_edges AS (
        SELECT DISTINCT pv.src_eid, pv.tgt_eid, pv.ord, d.properties::text AS eprops
        FROM pair_vids pv
        JOIN {gn}."DIRECTED" d
          ON (d.start_id = pv.src_vid AND d.end_id = pv.tgt_vid)
          OR (d.start_id = pv.tgt_vid AND d.end_id = pv.src_vid)
    ),
    -- All unique entity names from both sides
    all_eids AS (
        SELECT DISTINCT eid FROM (
            SELECT src_eid AS eid FROM input_pairs
            UNION
            SELECT tgt_eid AS eid FROM input_pairs
        ) t
    ),
    -- Node properties for endpoint entities
    entity_props AS (
        SELECT
            ag_catalog.agtype_access_operator(
                VARIADIC ARRAY[b.properties, '"entity_id"'::agtype]
            )::text AS eid,
            b.properties::text AS props
        FROM {gn}.base b
        JOIN all_eids ae ON ag_catalog.agtype_access_operator(
            VARIADIC ARRAY[b.properties, '"entity_id"'::agtype]
        ) = (to_json(ae.eid)::text)::agtype
    )
    -- Edges then Entities
    SELECT * FROM (
        SELECT 'E'::text AS rtype, me.src_eid AS col1, me.tgt_eid AS col2, me.eprops AS col3
        FROM matched_edges me
        ORDER BY me.ord
    ) edge_rows

    UNION ALL

    SELECT 'N'::text AS rtype, ep.eid AS col1, ep.props AS col2, NULL AS col3
    FROM entity_props ep;
    """

    results = await graph_storage._query(
        query, params={"src_ids": src_ids, "tgt_ids": tgt_ids}
    )
    elapsed = time.time() - t0

    # Build VDB lookup for ordering and created_at
    vdb_lookup = {(r["src_id"], r["tgt_id"]): r for r in vdb_results}

    # Parse edges (preserve VDB order)
    edge_map: dict[tuple[str, str], dict] = {}  # (src, tgt) → props
    for row in results:
        if row.get("rtype") != "E":
            continue
        src = _strip_agtype_quotes(row["col1"])
        tgt = _strip_agtype_quotes(row["col2"])
        eprops = row["col3"]
        if isinstance(eprops, str):
            try:
                eprops = json.loads(eprops)
            except json.JSONDecodeError:
                eprops = {}
        eprops = eprops or {}
        edge_map[(src, tgt)] = eprops

    # Reconstruct edge_datas in VDB result order
    edge_datas: list[dict[str, Any]] = []
    for vdb_r in vdb_results:
        pair = (vdb_r["src_id"], vdb_r["tgt_id"])
        eprops = edge_map.get(pair)
        if eprops is None:
            continue
        if "weight" not in eprops:
            eprops["weight"] = 1.0
        edge_datas.append({
            "src_id": pair[0],
            "tgt_id": pair[1],
            "created_at": vdb_r.get("created_at"),
            **eprops,
        })

    # Parse entities
    entity_datas: list[dict[str, Any]] = []
    seen_entities: set[str] = set()
    # Preserve entity order from edges (src first, then tgt)
    ordered_names: list[str] = []
    for e in edge_datas:
        for name in (e["src_id"], e["tgt_id"]):
            if name not in seen_entities:
                seen_entities.add(name)
                ordered_names.append(name)

    entity_props_map: dict[str, dict] = {}
    for row in results:
        if row.get("rtype") != "N":
            continue
        eid = _strip_agtype_quotes(row["col1"])
        props = row["col2"]
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except json.JSONDecodeError:
                props = {}
        entity_props_map[eid] = props

    for name in ordered_names:
        props = entity_props_map.get(name)
        if props is not None:
            entity_datas.append({**props, "entity_name": name})

    logger.info(
        "[Perf] pg_get_edge_data_combined: %.3fs → %d edges, %d entities (1 SQL)",
        elapsed, len(edge_datas), len(entity_datas),
    )
    return edge_datas, entity_datas


def _dollar_quote(s: str) -> str:
    """Wrap string in PostgreSQL dollar-quoting for safe embedding in SQL."""
    tag = "$$"
    i = 0
    while tag in s:
        i += 1
        tag = f"${i}$"
    return f"{tag}{s}{tag}"


def _strip_agtype_quotes(val: str | None) -> str:
    """Strip surrounding double-quotes added by AGE agtype→text cast."""
    if not val:
        return ""
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]
    return val
