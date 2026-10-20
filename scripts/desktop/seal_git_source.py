"""Запечатать tracked Git checkout для сборки: repository output-directory.

Использует ту же строгую побайтовую проверку Git blob, что frozen API CI.
Входы SDK, рабочие CAD/БД и локальные данные в manifest не попадают.
"""

import ast
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys

root, output = (
    pathlib.Path(sys.argv[1]).resolve(),
    pathlib.Path(os.path.abspath(sys.argv[2])),
)
output.relative_to(root)
for parent in (output, *output.parents):
    if parent.exists():
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Source seal output не допускает reparse point")
    if parent == root:
        break
output.mkdir(parents=True, exist_ok=True)


def git(*arguments):
    return subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, check=True
    ).stdout


head = git("rev-parse", "HEAD").decode("ascii").strip()
expected = os.environ.get("GITHUB_SHA") or head
if head != expected or git("status", "--porcelain=v1", "--untracked-files=no"):
    raise ValueError("Для frozen API нужен неизменённый закреплённый Git checkout")
algorithm = git("rev-parse", "--show-object-format").decode("ascii").strip()
if algorithm not in {"sha1", "sha256"}:
    raise ValueError("Неизвестный формат Git objects")
rows = []
for record in git("ls-tree", "-r", "-z", "--full-tree", head).split(b"\0"):
    if not record:
        continue
    description, encoded_path = record.split(b"\t", 1)
    mode, kind, object_id = description.decode("ascii").split()
    relative = encoded_path.decode("utf-8")
    parts = pathlib.PurePosixPath(relative).parts
    if mode not in {"100644", "100755"} or kind != "blob":
        raise ValueError(
            "Source seal допускает только обычные tracked файлы: " + relative
        )
    forbidden = {".git", ".runtime", ".venv", "node_modules", "__pycache__", "output"}
    if (
        not parts
        or any(part in forbidden for part in parts)
        or parts[0].lower() in {"sdk", "objectarx"}
    ):
        raise ValueError("Developer/SDK данные не входят в source seal: " + relative)
    path = root.joinpath(*parts)
    if path.suffix.lower() in {".db", ".db3", ".sqlite", ".sqlite3"}:
        raise ValueError("Рабочие базы не входят в source seal: " + relative)
    if path.suffix.lower() in {".dwg", ".dxf"} and parts[0] != "fixtures":
        raise ValueError(
            "CAD вне версионированных fixtures не входит в source seal: " + relative
        )
    for current in (path, *path.parents):
        if current == root:
            break
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Source seal не допускает ссылку/reparse: " + relative)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Source seal ожидает обычный файл: " + relative)
    size = path.stat().st_size
    blob = hashlib.new(algorithm)
    blob.update(b"blob " + str(size).encode("ascii") + b"\0")
    digest, observed = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            observed += len(block)
            blob.update(block)
            digest.update(block)
    if observed != size or blob.hexdigest() != object_id:
        raise ValueError(
            "Checkout bytes не совпадают с закреплённым Git blob: " + relative
        )
    rows.append({"path": relative, "sha256": digest.hexdigest(), "bytes": size})
rows.sort(key=lambda row: row["path"])
paths = {row["path"] for row in rows}
tree = ast.parse(
    (root / "apps/api/app/distribution/preflight.py").read_text(encoding="utf-8")
)
resources = next(
    ast.literal_eval(node.value)
    for node in tree.body
    if isinstance(node, ast.Assign)
    and any(
        isinstance(target, ast.Name) and target.id == "RUNTIME_RESOURCES"
        for target in node.targets
    )
)
required = {"tools/autocad-bridge/desktop/entry.py", "apps/api/app/__init__.py"} | {
    "apps/api/app/" + value for value in resources
}
if not required <= paths:
    raise ValueError("Git checkout не содержит обязательные runtime исходники")
manifest = output / "source-manifest.json"
with manifest.open("x", encoding="utf-8", newline="\n") as stream:
    json.dump({"files": rows}, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
receipt = {
    "schema": "green-atlas.frozen-api-git-source/1",
    "git_commit": head,
    "git_tree": git("rev-parse", head + "^{tree}").decode("ascii").strip(),
    "git_object_format": algorithm,
    "git_blob_bytes_match": True,
    "source_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    "tracked_regular_files": len(rows),
    "app_python_files": sum(
        path.startswith("apps/api/app/") and path.endswith(".py") for path in paths
    ),
    "runtime_raw_resources": len(resources),
    "product_qualified": False,
}
with (output / "git-source-receipt.json").open("x", encoding="utf-8") as stream:
    json.dump(receipt, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
print(json.dumps(receipt, ensure_ascii=False))
