"""
Visual graph serializers and view models for Vis.js canvas rendering.
Extracted from app.py to keep API routes declarative, lean, and under 500 lines.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from cortex.symbols.graph import SymbolGraphManager
from cortex.falkordb_client import get_shared_falkordb_client


def build_graph_view(
    host: str,
    port: int,
    default_db: str,
    graph_type: str = "episodic",
    clean: bool = True,
    graph_name: Optional[str] = None,
) -> dict:
    """
    Queries FalkorDB for nodes and relationships formatted for vis-network.
    graph_type: 'episodic' (cortex db) or 'symbols' (cortex_symbols db).
    clean: If True (default for symbols), filters out external built-ins.
    """
    nodes_dict: Dict[str, dict] = {}
    edges_list: List[dict] = []

    if graph_name:
        target_db = graph_name
        is_symbols_graph = "symbol" in graph_name.lower() or graph_type == "symbols"
    else:
        target_db = "cortex_symbols" if graph_type == "symbols" else default_db
        is_symbols_graph = graph_type == "symbols"

    try:
        client = get_shared_falkordb_client(host=host, port=port)
        r = client.get_redis()

        if is_symbols_graph:
            if clean:
                # Clean View: Fetch project-defined symbols and files
                nodes_res = r.execute_command(
                    "GRAPH.QUERY",
                    target_db,
                    "MATCH (s:Symbol)-[d:DEFINED_IN]->(f:File) RETURN s.name, f.path, d.kind, d.line",
                )
                if nodes_res and len(nodes_res) > 1 and isinstance(nodes_res[1], list):
                    for row in nodes_res[1]:
                        if len(row) >= 4:
                            sym_name, file_path, kind, line = str(row[0]), str(row[1]), str(row[2]), str(row[3])
                            is_doc = file_path.lower().endswith((".md", ".markdown"))
                            if file_path not in nodes_dict:
                                nodes_dict[file_path] = {
                                    "id": file_path,
                                    "label": Path(file_path).stem.replace("-", " ").replace("_", " ").title() if is_doc else Path(file_path).name,
                                    "full_name": file_path,
                                    "group": "document" if is_doc else "file",
                                    "title": f"<b>{'Document' if is_doc else 'File'}: {Path(file_path).name}</b><br/>{file_path}",
                                }
                            if sym_name not in nodes_dict:
                                nodes_dict[sym_name] = {
                                    "id": sym_name,
                                    "label": sym_name,
                                    "full_name": sym_name,
                                    "group": "symbol",
                                    "title": f"<b>{sym_name}</b> ({kind})<br/>Line {line} in {Path(file_path).name}",
                                }
                            edges_list.append({
                                "from": sym_name,
                                "to": file_path,
                                "label": "DEFINED_IN",
                                "arrows": "to",
                                "font": {"size": 9, "align": "middle", "color": "#38bdf8"},
                                "color": {"color": "#38bdf8", "highlight": "#ffffff"},
                            })

                # Internal references between project files and project symbols
                refs_res = r.execute_command(
                    "GRAPH.QUERY",
                    target_db,
                    "MATCH (caller:File)-[r:REFERENCES]->(s:Symbol)-[:DEFINED_IN]->(target:File) RETURN caller.path, s.name, r.line",
                )
                if refs_res and len(refs_res) > 1 and isinstance(refs_res[1], list):
                    for row in refs_res[1]:
                        if len(row) >= 3:
                            caller_path, sym_name, line = str(row[0]), str(row[1]), str(row[2])
                            if caller_path in nodes_dict and sym_name in nodes_dict:
                                edges_list.append({
                                    "from": caller_path,
                                    "to": sym_name,
                                    "label": "REFERENCES",
                                    "arrows": "to",
                                    "font": {"size": 9, "align": "middle", "color": "#34d399"},
                                    "color": {"color": "#34d399", "highlight": "#ffffff"},
                                })

                # Project imports
                imports_res = r.execute_command(
                    "GRAPH.QUERY",
                    target_db,
                    "MATCH (f:File)-[r:IMPORTS]->(s:Symbol) WHERE (s)-[:DEFINED_IN]->() RETURN f.path, s.name, r.line",
                )
                if imports_res and len(imports_res) > 1 and isinstance(imports_res[1], list):
                    for row in imports_res[1]:
                        if len(row) >= 3:
                            f_path, sym_name, line = str(row[0]), str(row[1]), str(row[2])
                            if f_path in nodes_dict and sym_name in nodes_dict:
                                edges_list.append({
                                    "from": f_path,
                                    "to": sym_name,
                                    "label": "IMPORTS",
                                    "arrows": "to",
                                    "font": {"size": 9, "align": "middle", "color": "#a78bfa"},
                                    "color": {"color": "#a78bfa", "highlight": "#ffffff"},
                                })

                # SCIP IMPLEMENTS relations
                impl_res = r.execute_command(
                    "GRAPH.QUERY",
                    target_db,
                    "MATCH (s1:Symbol)-[r:IMPLEMENTS]->(s2:Symbol) RETURN s1.name, s2.name",
                )
                if impl_res and len(impl_res) > 1 and isinstance(impl_res[1], list):
                    for row in impl_res[1]:
                        if len(row) >= 2:
                            s1, s2 = str(row[0]), str(row[1])
                            if s1 not in nodes_dict:
                                nodes_dict[s1] = {"id": s1, "label": s1, "full_name": s1, "group": "symbol", "title": f"<b>{s1}</b> (Class)"}
                            if s2 not in nodes_dict:
                                nodes_dict[s2] = {"id": s2, "label": s2, "full_name": s2, "group": "symbol", "title": f"<b>{s2}</b> (Interface)"}
                            edges_list.append({
                                "from": s1,
                                "to": s2,
                                "label": "IMPLEMENTS",
                                "arrows": "to",
                                "dashes": True,
                                "font": {"size": 9, "align": "middle", "color": "#ec4899"},
                                "color": {"color": "#ec4899", "highlight": "#f472b6"},
                            })

                # SCIP TYPE_OF relations
                type_res = r.execute_command(
                    "GRAPH.QUERY",
                    target_db,
                    "MATCH (s1:Symbol)-[r:TYPE_OF]->(s2:Symbol) RETURN s1.name, s2.name",
                )
                if type_res and len(type_res) > 1 and isinstance(type_res[1], list):
                    for row in type_res[1]:
                        if len(row) >= 2:
                            s1, s2 = str(row[0]), str(row[1])
                            if s1 not in nodes_dict:
                                nodes_dict[s1] = {"id": s1, "label": s1, "full_name": s1, "group": "symbol", "title": f"<b>{s1}</b>"}
                            if s2 not in nodes_dict:
                                nodes_dict[s2] = {"id": s2, "label": s2, "full_name": s2, "group": "symbol", "title": f"<b>{s2}</b> (Type)"}
                            edges_list.append({
                                "from": s1,
                                "to": s2,
                                "label": "TYPE_OF",
                                "font": {"size": 9, "align": "middle", "color": "#f59e0b"},
                                "color": {"color": "#f59e0b", "highlight": "#fbbf24"},
                            })

                # Markdown Document Links (LINKS_TO)
                doc_links_res = r.execute_command(
                    "GRAPH.QUERY",
                    target_db,
                    "MATCH (d:Document)-[r:LINKS_TO]->(target) "
                    "RETURN d.path, coalesce(d.title, d.path), coalesce(target.path, target.name), coalesce(target.title, target.name, target.path), labels(target), coalesce(r.label, 'LINKS_TO'), coalesce(r.link_type, 'link')",
                )
                if doc_links_res and len(doc_links_res) > 1 and isinstance(doc_links_res[1], list):
                    for row in doc_links_res[1]:
                        if len(row) >= 7:
                            src_path, src_title, dst_id, dst_title, dst_labels, rel_label, link_type = (
                                str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]), str(row[5]), str(row[6])
                            )
                            if src_path in nodes_dict:
                                nodes_dict[src_path]["group"] = "document"
                                if src_title and src_title != src_path:
                                    nodes_dict[src_path]["label"] = src_title
                                    nodes_dict[src_path]["title"] = f"<b>Document: {src_title}</b><br/>{src_path}"
                            else:
                                nodes_dict[src_path] = {
                                    "id": src_path,
                                    "label": src_title or Path(src_path).stem,
                                    "full_name": src_path,
                                    "group": "document",
                                    "title": f"<b>Document: {src_title}</b><br/>{src_path}",
                                }
                            is_concept = "concept" in dst_labels.lower()
                            is_doc = "document" in dst_labels.lower()
                            dst_group = "concept" if is_concept else ("document" if is_doc else "file")
                            if dst_id in nodes_dict:
                                nodes_dict[dst_id]["group"] = dst_group
                            else:
                                nodes_dict[dst_id] = {
                                    "id": dst_id,
                                    "label": dst_title or Path(dst_id).name,
                                    "full_name": dst_id,
                                    "group": dst_group,
                                    "title": f"<b>{dst_title}</b> ({dst_group})<br/>{dst_id}",
                                }
                            edges_list.append({
                                "from": src_path,
                                "to": dst_id,
                                "label": rel_label if rel_label != "LINKS_TO" else link_type,
                                "arrows": "to",
                                "font": {"size": 9, "align": "middle", "color": "#06b6d4"},
                                "color": {"color": "#06b6d4", "highlight": "#22d3ee"},
                            })

                # Markdown Document Tags (HAS_TAG)
                tags_res = r.execute_command(
                    "GRAPH.QUERY",
                    target_db,
                    "MATCH (d:Document)-[r:HAS_TAG]->(t:Tag) RETURN d.path, coalesce(d.title, d.path), t.name",
                )
                if tags_res and len(tags_res) > 1 and isinstance(tags_res[1], list):
                    for row in tags_res[1]:
                        if len(row) >= 3:
                            src_path, src_title, tag_name = str(row[0]), str(row[1]), str(row[2])
                            if src_path not in nodes_dict:
                                nodes_dict[src_path] = {
                                    "id": src_path,
                                    "label": src_title or Path(src_path).stem,
                                    "full_name": src_path,
                                    "group": "document",
                                    "title": f"<b>Document: {src_title}</b><br/>{src_path}",
                                }
                            tag_id = f"tag:{tag_name}"
                            if tag_id not in nodes_dict:
                                nodes_dict[tag_id] = {
                                    "id": tag_id,
                                    "label": f"#{tag_name}",
                                    "full_name": tag_name,
                                    "group": "tag",
                                    "title": f"<b>Tag: #{tag_name}</b>",
                                }
                            edges_list.append({
                                "from": src_path,
                                "to": tag_id,
                                "label": "TAG",
                                "arrows": "to",
                                "dashes": [2, 2],
                                "font": {"size": 9, "align": "middle", "color": "#f59e0b"},
                                "color": {"color": "#f59e0b", "highlight": "#fbbf24"},
                            })
            else:
                # Full Raw View (all symbols, including external calls)
                nodes_res = r.execute_command(
                    "GRAPH.QUERY",
                    target_db,
                    "MATCH (n) RETURN coalesce(n.name, n.path), labels(n)",
                )
                if nodes_res and len(nodes_res) > 1 and isinstance(nodes_res[1], list):
                    for row in nodes_res[1]:
                        if len(row) >= 2:
                            raw_id = str(row[0])
                            raw_label = str(row[1]).replace("[", "").replace("]", "").replace("'", "").strip()
                            is_file = "file" in raw_label.lower()
                            group = "file" if is_file else "symbol"
                            display_name = Path(raw_id).name if is_file else raw_id

                            nodes_dict[raw_id] = {
                                "id": raw_id,
                                "label": display_name,
                                "full_name": raw_id,
                                "group": group,
                                "title": f"<b>{display_name}</b><br/>Type: {raw_label}<br/>Path: {raw_id}",
                            }

                edges_res = r.execute_command(
                    "GRAPH.QUERY",
                    target_db,
                    "MATCH (s)-[r]->(t) RETURN coalesce(s.name, s.path), type(r), coalesce(t.name, t.path)",
                )
                if edges_res and len(edges_res) > 1 and isinstance(edges_res[1], list):
                    for row in edges_res[1]:
                        if len(row) >= 3:
                            src = str(row[0])
                            rel = str(row[1])
                            dst = str(row[2])
                            if src not in nodes_dict:
                                nodes_dict[src] = {"id": src, "label": Path(src).name if "/" in src else src, "full_name": src, "group": "symbol", "title": src}
                            if dst not in nodes_dict:
                                nodes_dict[dst] = {"id": dst, "label": Path(dst).name if "/" in dst else dst, "full_name": dst, "group": "file", "title": dst}

                            color_map = {
                                "DEFINED_IN": "#38bdf8",
                                "IMPORTS": "#a78bfa",
                                "REFERENCES": "#34d399",
                                "IMPLEMENTS": "#ec4899",
                                "TYPE_OF": "#f59e0b",
                            }
                            edge_color = color_map.get(rel, "#94a3b8")
                            edges_list.append({
                                "from": src,
                                "to": dst,
                                "label": rel,
                                "arrows": "to",
                                "dashes": True if rel == "IMPLEMENTS" else ([4, 4] if rel == "TYPE_OF" else False),
                                "font": {"size": 9, "align": "middle", "color": edge_color},
                                "color": {"color": edge_color, "highlight": "#ffffff"},
                            })

        else:
            # Query all episodic nodes
            nodes_res = r.execute_command("GRAPH.QUERY", target_db, "MATCH (n) RETURN n.name, labels(n)")
            if nodes_res and len(nodes_res) > 1 and isinstance(nodes_res[1], list):
                for row in nodes_res[1]:
                    if len(row) >= 2:
                        name = str(row[0])
                        raw_label = str(row[1]).replace("[", "").replace("]", "").replace("'", "").strip()
                        is_episodic = "episodic" in raw_label.lower()
                        group = "episodic" if is_episodic else "entity"
                        display_label = (name[:26] + "…") if len(name) > 28 else name

                        nodes_dict[name] = {
                            "id": name,
                            "label": display_label,
                            "full_name": name,
                            "group": group,
                            "title": f"<b>{name}</b><br/>Type: {raw_label}",
                        }

            # Query all edges
            edges_res = r.execute_command(
                "GRAPH.QUERY",
                target_db,
                "MATCH (s)-[r]->(t) RETURN s.name, type(r), t.name",
            )
            if edges_res and len(edges_res) > 1 and isinstance(edges_res[1], list):
                for row in edges_res[1]:
                    if len(row) >= 3:
                        src = str(row[0])
                        rel = str(row[1])
                        dst = str(row[2])
                        if src not in nodes_dict:
                            nodes_dict[src] = {"id": src, "label": src, "full_name": src, "group": "entity", "title": src}
                        if dst not in nodes_dict:
                            nodes_dict[dst] = {"id": dst, "label": dst, "full_name": dst, "group": "entity", "title": dst}

                        is_mention = rel == "MENTIONS"
                        edges_list.append({
                            "from": src,
                            "to": dst,
                            "label": rel,
                            "is_mention": is_mention,
                            "arrows": "to",
                            "font": {"size": 9, "align": "middle", "color": "#64748b" if is_mention else "#a5b4fc"},
                        })

        return {
            "status": "ok",
            "graph_type": graph_type,
            "nodes": list(nodes_dict.values()),
            "edges": edges_list,
            "stats": {
                "nodes_count": len(nodes_dict),
                "edges_count": len(edges_list),
            },
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "nodes": [], "edges": []}


def build_symbol_impact_view(
    target: Optional[str],
    file_path: Optional[str],
    symbol: Optional[str],
    direction: str,
    max_depth: int,
    host: str,
    port: int,
) -> dict:
    """
    Performs blast-radius analysis and formats structured nodes/edges/tree for visualization.
    """
    raw_target = (target or symbol or file_path or "").strip()
    if not raw_target:
        return {"status": "error", "message": "Target symbol or file path is required"}

    depth = max(1, min(int(max_depth), 5))
    is_file = "/" in raw_target or raw_target.endswith((".py", ".ts", ".js", ".go", ".rs", ".cpp", ".c", ".h", ".java"))

    nodes_map: Dict[str, dict] = {}
    edges_list: List[dict] = []
    tree_items: List[dict] = []

    COLOR_TARGET = {"background": "#6366f1", "border": "#4338ca", "highlight": {"background": "#818cf8", "border": "#6366f1"}}
    COLOR_HOP1 = {"background": "#ef4444", "border": "#b91c1c", "highlight": {"background": "#f87171", "border": "#ef4444"}}
    COLOR_HOP2 = {"background": "#f59e0b", "border": "#b45309", "highlight": {"background": "#fbbf24", "border": "#f59e0b"}}
    COLOR_HOP3 = {"background": "#38bdf8", "border": "#0284c7", "highlight": {"background": "#7dd3fc", "border": "#38bdf8"}}

    try:
        sym_mgr = SymbolGraphManager()

        if is_file:
            file_name = Path(raw_target).name
            nodes_map[raw_target] = {
                "id": raw_target,
                "label": file_name,
                "full_name": raw_target,
                "group": "file",
                "color": COLOR_TARGET,
                "shape": "box",
                "font": {"color": "#ffffff", "bold": True},
                "title": f"<b>Target File: {file_name}</b><br/>{raw_target}",
                "level": 0,
            }

            impacts = sym_mgr.get_impact_tree(raw_target)
            for imp in impacts:
                dep_path = imp.get("file_path", "")
                dep_name = Path(dep_path).name
                sym_name = imp.get("symbol", "")
                rel = imp.get("relation", "DEPENDS_ON")
                line = imp.get("line", 1)

                if dep_path not in nodes_map:
                    nodes_map[dep_path] = {
                        "id": dep_path,
                        "label": dep_name,
                        "full_name": dep_path,
                        "group": "file",
                        "color": COLOR_HOP1,
                        "shape": "box",
                        "font": {"color": "#ffffff"},
                        "title": f"<b>Impacted File: {dep_name}</b><br/>Calls {sym_name} (Line {line})",
                        "level": 1,
                    }

                edges_list.append({
                    "from": dep_path,
                    "to": raw_target,
                    "label": f"{rel} ({sym_name})",
                    "arrows": "to",
                    "font": {"size": 9, "align": "middle", "color": "#f87171"},
                    "color": {"color": "#ef4444", "highlight": "#ffffff"},
                })
                tree_items.append({
                    "file_path": dep_path,
                    "file_name": dep_name,
                    "symbol": sym_name,
                    "relation": rel,
                    "line": line,
                    "risk": "HIGH",
                    "depth": 1,
                })

            summary = {
                "target": raw_target,
                "type": "file",
                "direction": "upstream",
                "direct_impact_count": len(impacts),
                "transitive_count": 0,
                "affected_files_count": len({i.get("file_path") for i in impacts}),
                "risk_level": "HIGH" if len(impacts) > 5 else ("MEDIUM" if impacts else "LOW"),
            }

        else:
            trace = sym_mgr.trace_symbol_impact(raw_target, direction=direction, max_depth=depth)

            nodes_map[raw_target] = {
                "id": raw_target,
                "label": raw_target,
                "full_name": raw_target,
                "group": "symbol",
                "color": COLOR_TARGET,
                "shape": "box",
                "font": {"color": "#ffffff", "bold": True},
                "title": f"<b>Target Symbol: {raw_target}</b>",
                "level": 0,
            }

            for imp in trace.get("direct_impacts", []):
                src_file = imp.get("source_file", "")
                rel = imp.get("relation", "CALLS")
                line = imp.get("line", 1)
                file_label = Path(src_file).name

                if src_file not in nodes_map:
                    nodes_map[src_file] = {
                        "id": src_file,
                        "label": file_label,
                        "full_name": src_file,
                        "group": "file",
                        "color": COLOR_HOP1,
                        "shape": "box",
                        "font": {"color": "#ffffff"},
                        "title": f"<b>Direct Impact: {file_label}</b><br/>{rel} at line {line}",
                        "level": 1,
                    }

                edges_list.append({
                    "from": src_file if direction.lower() == "upstream" else raw_target,
                    "to": raw_target if direction.lower() == "upstream" else src_file,
                    "label": f"{rel}:{line}",
                    "arrows": "to",
                    "font": {"size": 9, "align": "middle", "color": "#f87171" if direction.lower() == "upstream" else "#38bdf8"},
                    "color": {"color": "#ef4444" if direction.lower() == "upstream" else "#38bdf8", "highlight": "#ffffff"},
                })

                tree_items.append({
                    "source": src_file,
                    "name": file_label,
                    "relation": rel,
                    "line": line,
                    "risk": "HIGH",
                    "depth": 1,
                })

            for t_item in trace.get("type_hierarchies", []):
                t_sym = t_item.get("symbol", "")
                t_rel = t_item.get("relation", "IMPLEMENTS")
                t_file = t_item.get("file_path", "")

                if t_sym not in nodes_map:
                    nodes_map[t_sym] = {
                        "id": t_sym,
                        "label": t_sym,
                        "full_name": t_sym,
                        "group": "symbol",
                        "color": COLOR_HOP2,
                        "shape": "ellipse",
                        "font": {"color": "#ffffff"},
                        "title": f"<b>Type Relation: {t_sym}</b><br/>{t_rel} in {Path(t_file).name}",
                        "level": 2,
                    }

                edges_list.append({
                    "from": t_sym if direction.lower() == "upstream" else raw_target,
                    "to": raw_target if direction.lower() == "upstream" else t_sym,
                    "label": t_rel,
                    "arrows": "to",
                    "dashes": True,
                    "font": {"size": 9, "align": "middle", "color": "#fbbf24"},
                    "color": {"color": "#f59e0b", "highlight": "#ffffff"},
                })

                tree_items.append({
                    "source": t_sym,
                    "name": t_sym,
                    "relation": t_rel,
                    "file_path": t_file,
                    "risk": "MEDIUM",
                    "depth": 2,
                })

            if depth >= 2:
                client = get_shared_falkordb_client(host=host, port=port)
                for imp in trace.get("direct_impacts", [])[:10]:
                    sf = imp.get("source_file")
                    trans_q = (
                        "MATCH (f2:File)-[r:IMPORTS]->(s2:Symbol)-[:DEFINED_IN]->(f1:File {path: $p}) "
                        "RETURN DISTINCT f2.path, s2.name, r.line LIMIT 10"
                    )
                    t_res = client.query("cortex_symbols", trans_q, {"p": sf})
                    if t_res and len(t_res) > 1 and isinstance(t_res[1], list):
                        for r_row in t_res[1]:
                            if len(r_row) >= 3:
                                f2_path = str(r_row[0])
                                f2_name = Path(f2_path).name
                                s2_name = str(r_row[1])
                                if f2_path not in nodes_map and f2_path != sf:
                                    nodes_map[f2_path] = {
                                        "id": f2_path,
                                        "label": f2_name,
                                        "full_name": f2_path,
                                        "group": "file",
                                        "color": COLOR_HOP2 if depth == 2 else COLOR_HOP3,
                                        "shape": "box",
                                        "font": {"color": "#ffffff"},
                                        "title": f"<b>Transitive Consumer: {f2_name}</b><br/>Imports {s2_name}",
                                        "level": 2,
                                    }
                                    edges_list.append({
                                        "from": f2_path,
                                        "to": sf,
                                        "label": f"IMPORTS ({s2_name})",
                                        "arrows": "to",
                                        "font": {"size": 8, "align": "middle", "color": "#94a3b8"},
                                        "color": {"color": "#64748b", "highlight": "#ffffff"},
                                    })
                                    tree_items.append({
                                        "source": f2_path,
                                        "name": f2_name,
                                        "relation": f"TRANSITIVE IMPORTS ({s2_name})",
                                        "risk": "MEDIUM",
                                        "depth": 2,
                                    })

            summary = {
                "target": raw_target,
                "type": "symbol",
                "direction": direction.lower(),
                "max_depth": depth,
                "definitions_count": len(trace.get("definitions", [])),
                "direct_impact_count": len(trace.get("direct_impacts", [])),
                "type_hierarchy_count": len(trace.get("type_hierarchies", [])),
                "total_impacted_locations": max(0, len(nodes_map) - 1),
                "risk_level": "HIGH" if len(trace.get("direct_impacts", [])) > 4 else ("MEDIUM" if len(nodes_map) > 1 else "LOW"),
            }

        return {
            "status": "ok",
            "target": raw_target,
            "summary": summary,
            "nodes": list(nodes_map.values()),
            "edges": edges_list,
            "tree": tree_items,
        }

    except Exception as e:
        return {"status": "error", "message": str(e), "nodes": [], "edges": [], "tree": []}
