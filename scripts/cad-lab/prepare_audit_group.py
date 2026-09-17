"""Stage already inspected DXFs for one exact, closed XREF group, using hardlinks."""

import hashlib
import json
import os
from pathlib import Path
import sys


def main(summary_path: Path, root_name: str):
    summary = json.loads(summary_path.read_text(encoding="utf8"))
    street = next(s for s in summary["streets"] if any(r["root"] == root_name for r in s["root_checks"]))
    group = next(r for r in street["root_checks"] if r["root"] == root_name)
    if not group["exact_links_readable"]:
        raise ValueError("Group has unread/unresolved references; it cannot be staged as closed")
    nodes = {n["path"]: n for n in street["files"]}
    base = Path(os.path.commonpath([str(Path(p).parent) for p in group["files"]]))
    folder = summary_path.parent / "groups" / hashlib.sha256(root_name.encode()).hexdigest()[:16]
    prepared = folder / "prepared"
    records = []
    for path in group["files"]:
        node = nodes[path]
        source = Path(node["dxf"])
        target = prepared / Path(path).relative_to(base).with_suffix(".dxf")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            os.link(source, target)
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if hashlib.sha256(target.read_bytes()).hexdigest() != source_hash:
            raise ValueError("Previously staged source does not match its conversion")
        records.append({"original": path, "original_sha256": node["sha256"],
                        "dxf": str(target.resolve()), "dxf_sha256": source_hash})
    result = {"root_original": root_name, "root": str((prepared / Path(root_name).relative_to(base).with_suffix(".dxf")).resolve()),
              "package": str(prepared.resolve()), "directory": str(folder.resolve()),
              "files": records, "note": "Hardlinks to diagnostic immutable copies, not original DWG files."}
    (folder / "profile.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
