"""Bounded owned UI evidence in job logs when the artifact service is unavailable."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import struct
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
RECEIPT_FIELDS = {
    "schema",
    "passed",
    "error",
    "ordinary_user_token",
    "product_qualified",
    "autocad_executed",
    "blender_executed",
    "setup_sha256",
    "source_manifest_sha256",
    "distribution_manifest_sha256",
    "steps",
    "stages",
    "screenshots",
    "finish_launched_installed_application",
    "installed_api_and_webview_session_ready",
    "native_update_preserved_database",
    "native_uninstall_preserved_database",
    "different_version_update_tested",
    "previous_version",
    "current_version",
    "old_executable_created_project",
    "new_executable_reopened_project",
    "update_preserved_database_bytes",
    "uninstall_preserved_database_bytes",
    "prepared_cad_project_migration_tested",
    "wizard_component_paths_saved",
    "wizard_browse_buttons_tested",
    "wizard_browse_accept_fields",
    "wizard_browse_cancel_preserved_fields",
    "component_settings_preserved_by_update_and_uninstall",
    "component_libraries_are_ci_fixtures",
    "component_ui",
}
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_COUNT = 40


def regular(path, root):
    if not path.is_relative_to(root):
        raise ValueError("Evidence escaped its own root")
    for candidate in (path, *path.parents):
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Evidence contains a link or reparse point")
        if candidate == root:
            break
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Expected a regular evidence file")


def emit(root, write=print):
    total, count = 0, 0
    for name in ("install-smoke", "wizard-smoke", "upgrade-smoke"):
        directory = root / name / "user-checks"
        path = directory / "install-ci-receipt.json"
        if path.is_file():
            regular(path, root)
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("Oversized receipt")
            receipt = json.loads(path.read_bytes())
            selected = {
                key: value for key, value in receipt.items() if key in RECEIPT_FIELDS
            }
            if isinstance(selected.get("component_ui"), dict):
                component = selected["component_ui"]
                selected["component_ui"] = {
                    key: component[key]
                    for key in (
                        "passed",
                        "fixture_libraries",
                        "actual_autocad",
                        "actual_blender",
                        "native_pickers",
                        "native_picker_accept_fields",
                        "native_picker_cancel_preserved_fields",
                        "blender_invalid_rejected",
                        "reopened_settings_equal",
                        "steps",
                    )
                    if key in component
                }
                if isinstance(component.get("error"), str):
                    selected["component_ui"]["error"] = re.sub(
                        r"[A-Za-z0-9_-]{43,}", "[hidden]", component["error"][:2000]
                    )
                if not component.get("passed") and isinstance(
                    component.get("controls"), list
                ):
                    selected["component_ui"]["controls"] = [
                        {
                            key: re.sub(
                                r"[A-Za-z0-9_-]{43,}",
                                "[hidden]",
                                str(row.get(key, ""))[:400],
                            )
                            for key in ("name", "type")
                        }
                        for row in component["controls"][:80]
                        if isinstance(row, dict)
                    ]
            # Error text is diagnostic, never a channel for session credentials.
            if isinstance(selected.get("error"), str):
                selected["error"] = re.sub(
                    r"[A-Za-z0-9_-]{43,}", "[hidden]", selected["error"]
                )
            if "screenshots" in selected:
                selected["screenshots"] = [
                    {"file": Path(row["file"]).name, "sha256": row["sha256"]}
                    for row in selected["screenshots"]
                ]
            write(
                "GA_CI_RECEIPT "
                + json.dumps({"id": name, "receipt": selected}, ensure_ascii=True)
            )
        if name != "wizard-smoke":
            continue
        for path in sorted(directory.glob("*.png")):
            regular(path, root)
            size = path.stat().st_size
            if (
                size > MAX_IMAGE_BYTES
                or total + size > MAX_TOTAL_IMAGE_BYTES
                or count >= MAX_IMAGE_COUNT
            ):
                raise ValueError("UI image evidence exceeds its bounded budget")
            data = path.read_bytes()
            if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError("Expected an original PNG frame")
            width, height = struct.unpack(">II", data[16:24])
            if not (0 < width <= 4096 and 0 < height <= 4096):
                raise ValueError("Unexpected UI frame dimensions")
            metadata = {
                "id": name + "/" + path.name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "width": width,
                "height": height,
                "source_commit": os.environ.get("GITHUB_SHA"),
            }
            write("GA_CI_IMAGE_BEGIN " + json.dumps(metadata))
            encoded = base64.b64encode(data).decode("ascii")
            for offset in range(0, len(encoded), 4096):
                write("GA_CI_IMAGE_DATA " + encoded[offset : offset + 4096])
            write("GA_CI_IMAGE_END " + metadata["id"])
            total += size
            count += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.absolute()
    if root != REPOSITORY / ".runtime/autocad-bridge/ci-product":
        raise ValueError("Only the fixed owned CI product output is allowed")
    for candidate in (root, *root.parents):
        if candidate.exists():
            info = candidate.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                raise ValueError("CI evidence root contains a link or reparse point")
        if candidate == REPOSITORY:
            break
    emit(root)


if __name__ == "__main__":
    main()
