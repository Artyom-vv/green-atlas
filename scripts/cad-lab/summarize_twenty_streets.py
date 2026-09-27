"""Summarize independent street graphs without inventing XREF bindings.

Relative paths are authoritative. Basename matches are only review candidates,
never a claim that references were resolved or that drawings are complete.
"""

from collections import Counter, defaultdict
import json
from pathlib import Path, PureWindowsPath
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.cad_import.references import path_key


def summarize(directory: Path, manifest_name: str = "manifest.json") -> dict:
    manifest = json.loads((directory / manifest_name).read_text(encoding="utf8"))
    package_reports = json.loads((ROOT / "docs/implementation/2026-09-15-official-dataset/dataset/cad-package-manifests.json").read_text(encoding="utf8"))
    streets = []
    for street in manifest["streets"]:
        rows = [r for r in manifest["files"] if r["street"] == street["street"]]
        by_path = {path_key(r["path"]): r for r in rows}
        by_name = defaultdict(list)
        for row in rows:
            by_name[path_key(PureWindowsPath(row["path"]).name)].append(row["path"])
        nodes, edges = [], []
        for row in rows:
            folder = directory / "drawings" / row["sha256"]
            retry = directory / "retry-4g" / "drawings" / row["sha256"]
            if (retry / "result.json").exists():
                retried = json.loads((retry / "result.json").read_text(encoding="utf8"))
                if retried["status"] == "readable":
                    folder = retry
            receipt_path = folder / "result.json"
            node = {**row, "status": "pending"}
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text(encoding="utf8"))
                node.update(status=receipt["status"], seconds=receipt["seconds"],
                            error=receipt.get("error") or ("No readable nonempty DXF was produced" if receipt["status"] != "readable" else None), dxf=receipt.get("dxf"),
                            dxf_bytes=receipt.get("dxf_bytes"), attempts=receipt["attempts"])
                node["receipt"] = str(receipt_path)
                node["inspection_memory_budget_mib"] = 4096 if folder == retry else 2048
                inventory_path = folder / "inventory.json"
                if node["status"] == "readable":
                    inventory = json.loads(inventory_path.read_text(encoding="utf8"))
                    node.update(inventory=inventory)
                    for ref in inventory["xrefs"]:
                        requested = PureWindowsPath(ref["requested"])
                        relative_target = path_key(str(PureWindowsPath(row["path"]).parent / requested))
                        exact = by_path.get(relative_target) if not requested.drive and not requested.is_absolute() else None
                        edge = {"owner": row["path"], **ref,
                                "target": exact["path"] if exact else None,
                                "resolution": "exact_relative" if exact else "unresolved",
                                "same_street_name_candidates": by_name[path_key(requested.name)] if not exact else []}
                        candidate_hashes = sorted({by_path[path_key(p)]["sha256"] for p in edge["same_street_name_candidates"]})
                        edge["candidate_content_hashes"] = candidate_hashes
                        if not exact:
                            edge["review_class"] = ("same_name_single_content" if len(candidate_hashes) == 1
                                                    else "same_name_multiple_versions" if candidate_hashes
                                                    else "no_same_name_in_street")
                        edges.append(edge)
            nodes.append(node)

        # Exact active links only. Unresolved suggestions do not join groups.
        adjacency = {r["path"]: set() for r in rows}
        incoming = Counter()
        for edge in edges:
            if edge["active"] and edge["target"]:
                adjacency[edge["owner"]].add(edge["target"])
                adjacency[edge["target"]].add(edge["owner"])
                incoming[edge["target"]] += 1
        components, visited = [], set()
        for path in adjacency:
            if path in visited:
                continue
            queue, group = [path], []
            while queue:
                current = queue.pop()
                if current in visited:
                    continue
                visited.add(current)
                group.append(current)
                queue.extend(adjacency[current] - visited)
            components.append(sorted(group))

        by_node = {n["path"]: n for n in nodes}
        by_owner = defaultdict(list)
        for edge in edges:
            by_owner[edge["owner"]].append(edge)

        def closure(root):
            included, seen_contexts, unresolved, pending, failed, cyclic = set(), set(), [], set(), set(), []
            def visit(path, ancestors=()):
                if path in ancestors:
                    cyclic.append(path)
                    return
                context = (path, bool(ancestors))
                if context in seen_contexts:
                    return
                seen_contexts.add(context)
                included.add(path)
                node = by_node[path]
                if node["status"] == "pending":
                    pending.add(path)
                elif node["status"] != "readable":
                    failed.add(path)
                for edge in by_owner[path]:
                    if not edge["active"] or (ancestors and edge["overlay"]):
                        continue
                    if not edge["target"]:
                        unresolved.append(edge)
                    else:
                        visit(edge["target"], (*ancestors, path))
            visit(root)
            types = Counter()
            for path in included:
                types.update(by_node[path].get("inventory", {}).get("reachable_definition_types", {}))
            return {"root": root, "files": sorted(included), "unresolved": unresolved,
                    "pending": sorted(pending), "failed": sorted(failed), "cycles": cyclic,
                    "exact_links_readable": not (unresolved or pending or failed or cyclic),
                    "raw_definition_types_in_files": dict(types),
                    "note": "Definition inventory is not exploded geometry and may include cached nested overlays."}

        declared = []
        for report in package_reports:
            if report["street"] != street["street"] or not report["is_etransmit"]:
                continue
            report_path = report["path"].split("Пилотный проект 20 улиц/")[-1]
            for section, names in report["sections"].items():
                if not section.startswith("Базов"):
                    continue
                for name in names:
                    target = by_path.get(path_key(str(PureWindowsPath(report_path).parent / name)))
                    declared.append({"report": report_path, "declared": name,
                                     "matched_root": target["path"] if target else None})
        roots = sorted(set(p for p in adjacency if not incoming[p]) | {
            r["matched_root"] for r in declared if r["matched_root"]})
        streets.append({"street": street["street"], "counts": dict(Counter(n["status"] for n in nodes)),
                        "files": nodes, "xrefs": edges,
                        "exact_link_components": components,
                        "root_candidates": roots, "declared_packages": declared,
                        "root_checks": [closure(p) for p in roots],
                        "archives": street["archives"]})
    return {"scope": "All 20 streets, separate paths and graphs; exact links only",
            "complete_import_verified": False,
            "notes": ["SDK-readable does not prove conversion fidelity, normalization or editability.",
                      "Components are provisional where links are unresolved; not semantic project groups.",
                      "Nested overlays must be evaluated per root during assembly, not propagated blindly.",
                      "Definition counts are not multiplied by block insertions."],
            "streets": streets}


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    result = summarize(directory, sys.argv[2] if len(sys.argv) > 2 else "manifest.json")
    (directory / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    for street in result["streets"]:
        active = [e for e in street["xrefs"] if e["active"]]
        print(json.dumps({"street": street["street"], **street["counts"],
                          "active_links": len(active), "unresolved": sum(not e["target"] for e in active),
                          "provisional_components": len(street["exact_link_components"])}, ensure_ascii=False))
