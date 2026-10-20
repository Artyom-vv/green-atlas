"""Input completeness policy for the Core experiment, independent of CAD I/O."""

from collections import Counter
from typing import Any

XREF_RESOLVED = 1
XREF_UNLOADED = 2


def assess_input(before: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    """Keep unavailable dependencies distinct from deliberately unloaded refs."""
    issues = []
    unloaded = []
    for node in before["graph"]["nodes"][1:]:
        if node["status"] == XREF_UNLOADED:
            unloaded.append(node["name"])
        elif node["status"] != XREF_RESOLVED or not node["database_present"]:
            issues.append(
                {
                    "kind": "missing_xref",
                    "name": node["name"],
                    "path": node.get("authored_path", ""),
                    "native_status": node["status"],
                    "read_substatus": node["read_substatus"],
                }
            )
    classes = Counter(row["class"] for row in before["entities"])
    for row in inventory["objects"]:
        if row["error"] and not row["context"]:
            issues.append(
                {
                    "kind": "unreadable_object",
                    "route": row["route"],
                    "reason": row["error"],
                }
            )
    if any("Proxy" in name for name in classes):
        issues.append({"kind": "proxy_objects", "classes": dict(classes)})
    if before["database"]["units"] == 0:
        issues.append({"kind": "unknown_units"})
    return {
        "state": "needs_source_review" if issues or unloaded else "captured",
        "issues": issues,
        "unloaded_xrefs": unloaded,
        "classes": dict(classes),
        "instances": len(before["entities"]),
        "scope": "CAD readability only; does not establish engineering or normative completeness",
    }
