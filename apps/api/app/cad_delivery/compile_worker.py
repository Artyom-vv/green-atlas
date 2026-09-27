"""Server-side compilation of the exact native package. Never reads client paths."""

import json
import sys
from hashlib import sha256
from pathlib import Path

from app.cad_bridge import compile_region_probe
from app.cad_delivery.contracts import PublicationRequest, TransferManifest
from app.cad_intake.contracts import CadFingerprint, CadUploadPackage
from app.dxf_import.limits import MAX_CAD_SNAPSHOT_BYTES


def compile_package(
    directory: Path,
    manifest: TransferManifest,
    producer: PublicationRequest,
    plugin_version: str,
    root_id: str,
):
    drawings = {f.name: f for f in manifest.files if f.kind == "drawing"}
    snapshots = []
    # No fallback parser. A rejected native probe remains an explicit intake issue.
    failures = []
    package_bytes = sum(f.bytes for f in manifest.files)
    for name, drawing in drawings.items():
        try:
            raw = directory / f"{name}.green-atlas.geometry.json"
            if raw.stat().st_size > MAX_CAD_SNAPSHOT_BYTES:
                raise ValueError("Native probe exceeds memory admission size")
            probe = json.loads(raw.read_bytes())
            if probe.get("source", {}).get("sha256") != drawing.sha256:
                raise ValueError("Native probe does not match the uploaded drawing")
            if probe.get("plugin_version") != plugin_version:
                raise ValueError("Native probe producer differs from the transfer")
            for dependency in probe.get("xref_dependencies", []):
                if dependency.get("status") != "resolved":
                    continue
                matches = [
                    f
                    for f in drawings.values()
                    if f.sha256 == dependency.get("sha256")
                    and f.bytes == dependency.get("bytes")
                ]
                if not matches:
                    raise ValueError(
                        "A resolved native dependency is absent from the approved package"
                    )
                # Identical bytes are equivalent dependency evidence, regardless of
                # the original machine's path. Compiler verifies the bytes again.
                dependency["resolved_path"] = str(
                    directory / sorted(matches, key=lambda f: f.name)[0].name
                )
            snapshot = compile_region_probe(
                probe,
                autocad_version=producer.autocad_version,
                target=producer.target,
                package_root=directory,
            )
            payload = snapshot.model_dump_json(
                by_alias=True, exclude_none=True
            ).encode()
            if len(payload) > MAX_CAD_SNAPSHOT_BYTES:
                raise ValueError("Compiled snapshot exceeds admission size")
            if package_bytes + len(payload) > 4 * 1024**3:
                raise ValueError("Compiled package exceeds the 4 GiB storage budget")
            package_bytes += len(payload)
            snapshot_name = f"{name}.green-atlas.snapshot.json"
            (directory / snapshot_name).write_bytes(payload)
            snapshots.append(
                CadFingerprint(
                    root_id=root_id,
                    path=snapshot_name,
                    sha256=sha256(payload).hexdigest(),
                    bytes=len(payload),
                )
            )
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
            # Detail is a private server diagnostic; UI gets the normal intake review.
            failures.append(
                {"drawing": name, "error": type(error).__name__, "detail": str(error)}
            )
    package = CadUploadPackage(
        root_id=root_id,
        entries=[
            CadFingerprint(root_id=root_id, path=f.name, sha256=f.sha256, bytes=f.bytes)
            for f in drawings.values()
        ],
        snapshots=snapshots,
        total_bytes=sum(f.bytes for f in drawings.values())
        + sum(f.bytes for f in snapshots),
    )
    (directory / "upload.json").write_text(package.model_dump_json(), encoding="utf-8")
    (directory / "bridge-report.json").write_text(
        json.dumps({"failures": failures}, ensure_ascii=False), encoding="utf-8"
    )
    return package


def main():
    work = json.loads(Path(sys.argv[1]).read_text())
    compile_package(
        Path(work["directory"]),
        TransferManifest.model_validate(work["manifest"]),
        PublicationRequest.model_validate(work["producer"]),
        work["plugin_version"],
        work["root_id"],
    )


if __name__ == "__main__":
    main()
