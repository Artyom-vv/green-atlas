"""Accepted131 recovery is a pinned build input, never a fabricated build receipt."""

import hashlib
import json
import stat
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.desktop import windows_bundle as bundle


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, indent=2) + "\n").encode()


@pytest.fixture
def recovered(tmp_path, monkeypatch):
    root = tmp_path / "accepted131"
    root.mkdir()
    source_name = "tools/autocad-bridge/native/example.cpp"
    source = b"unchanged native source"
    plan_path = "tools/autocad-bridge/windows-2027/build-plan.json"
    source_files = {
        source_name: source,
        plan_path: encode({"schema": "green-atlas.native-build-plan/1", "targets": {
            "gui": {"sources": [source_name], "macos_adapters": []},
            "core": {"sources": [source_name]},
        }}),
        "tools/autocad-bridge/build-windows-2027.ps1": b"native build policy",
        "tools/autocad-bridge/build-macos.sh": b"mac build policy",
        "tools/autocad-bridge/build-query-worker-macos.sh": b"core build policy",
    }
    source_root = tmp_path / "current-source"
    for name, data in source_files.items():
        path = source_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    monkeypatch.setattr(bundle, "REPO", source_root)
    source_inventory = [{"path": name, "size": len(data), "sha256": sha(data)}
                        for name, data in source_files.items()]
    pins = dict(bundle.ACCEPTED_131)
    blobs = {path: b"signed CRT bytes" for path in bundle.ACCEPTED_131_FILES}
    blobs["Contents/Windows/dependency-inventory.json"] = b"{}\n"
    for target, binary, metadata, version in (
        ("gui", "GreenAtlasBridge.arx", "producer.json", "0.1.48"),
        ("core", "Worker/GreenAtlasQuery.crx", "Worker/worker.json", "0.1.49"),
    ):
        binary_data = (target + " unchanged PE fixture").encode()
        blobs["Contents/Windows/" + binary] = binary_data
        pins[target + "_binary_sha256"] = sha(binary_data)
        blobs["Contents/Windows/" + metadata] = encode(
            {
                "schema": "green-atlas.gui-producer/1"
                if target == "gui"
                else "green-atlas.query-worker/1",
                "target": "windows-x86_64",
                "binary": Path(binary).name,
                "plugin_version": version,
                "sha256": sha(binary_data),
            }
        )
        blobs["Contents/Windows/BuildEvidence/" + target + ".json"] = encode(
            {
                "schema": "green-atlas.packaged-native-evidence/1",
                "target": target,
                "source_manifest_sha256": pins["source_sha256"],
                "binary_sha256": sha(binary_data),
                "component_version": version,
                "files": source_inventory,
                "sdk_files": [{"path": "inc/adesk.h", "size": 12, "sha256": "1" * 64}],
                "execution_qualified": False,
            }
        )

    def repin():
        for target in ("gui", "core"):
            pins[target + "_evidence_sha256"] = sha(
                blobs["Contents/Windows/BuildEvidence/" + target + ".json"]
            )
        manifest = {
            "source": {"manifest_sha256": pins["source_sha256"]},
            "entries": [
                {
                    "path": name,
                    "kind": "file",
                    "size_bytes": len(data),
                    "sha256": sha(data),
                }
                for name, data in sorted(blobs.items())
                if name
                not in {"distribution-manifest.json", "RECOVERY-PROVENANCE.json"}
            ],
        }
        blobs["distribution-manifest.json"] = encode(manifest)
        pins["manifest_sha256"] = sha(blobs["distribution-manifest.json"])
        blobs["RECOVERY-PROVENANCE.json"] = encode(
            {
                "schema": "green-atlas.accepted131-native-recovery/1",
                "outer_installer_sha256": pins["installer_sha256"],
                "outer_installer_bytes": pins["installer_bytes"],
                "distribution_manifest_sha256": pins["manifest_sha256"],
                "original_native_build_receipts_present": False,
            }
        )
        pins["recovery_sha256"] = sha(blobs["RECOVERY-PROVENANCE.json"])
        flush()

    def flush():
        for name, data in blobs.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    repin()
    monkeypatch.setattr(bundle, "ACCEPTED_131", pins)
    rows = {name: SimpleNamespace(bytes=len(data), sha256=sha(data))
            for name, data in source_files.items()}
    return SimpleNamespace(
        root=root, blobs=blobs, rows=rows, pins=pins, repin=repin, flush=flush
    )


def test_pinned_recovery_preserves_raw_origin_without_build_receipts(
    recovered, tmp_path
):
    blobs, evidence = bundle.accepted_native_package(
        recovered.root, recovered.rows, "f" * 64
    )
    windows = tmp_path / "payload/Contents/Windows"
    (windows / "BuildEvidence").mkdir(parents=True)
    bundle.write_accepted_native(windows, blobs, evidence)
    with zipfile.ZipFile(windows / "BuildEvidence/accepted131-origin.zip") as archive:
        assert set(archive.namelist()) == bundle.ACCEPTED_131_FILES
        assert all(archive.read(name) == data for name, data in blobs.items())
        assert not any(
            name.endswith(("build-receipt.json", "source-inventory.json"))
            for name in archive.namelist()
        )
    for target in ("gui", "core"):
        assert evidence[target]["source_manifest_sha256"] == "f" * 64
        origin = evidence[target]["reused_from"]
        assert origin["source_kind"] == "recovered_accepted_release"
        assert origin["source_manifest_sha256"] == recovered.pins["source_sha256"]
        assert origin["new_native_compilation"] is False
        assert evidence[target]["execution_qualified"] is False
        assert origin["origin_archive_sha256"] == bundle.digest(
            windows / "BuildEvidence/accepted131-origin.zip"
        )
    # Verified buffers are used for output, not a second read of mutable input.
    (recovered.root / "Contents/Windows/GreenAtlasBridge.arx").write_bytes(
        b"changed after read"
    )
    assert (windows / "GreenAtlasBridge.arx").read_bytes() == blobs[
        "Contents/Windows/GreenAtlasBridge.arx"
    ]


@pytest.mark.parametrize("name", sorted(bundle.ACCEPTED_131_FILES))
def test_any_corrupt_origin_file_is_refused(recovered, name):
    (recovered.root / name).write_bytes(b"corrupted")
    with pytest.raises(ValueError):
        bundle.accepted_native_package(recovered.root, recovered.rows, "f" * 64)


@pytest.mark.parametrize(
    "name",
    [
        "Services/dadata.json",
        "profile.sqlite3",
        "extra",
        "EmptyDir/child",
        "contents/other",
    ],
)
def test_private_or_foreign_extra_entries_are_refused(recovered, name):
    path = recovered.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not an origin file")
    with pytest.raises(ValueError):
        bundle.accepted_native_package(recovered.root, recovered.rows, "f" * 64)


def test_extra_empty_directory_is_refused(recovered):
    (recovered.root / "other").mkdir()
    with pytest.raises(ValueError):
        bundle.accepted_native_package(recovered.root, recovered.rows, "f" * 64)


def test_changed_native_source_closure_is_refused(recovered):
    next(iter(recovered.rows.values())).sha256 = "e" * 64
    with pytest.raises(ValueError, match="source closure"):
        bundle.accepted_native_package(recovered.root, recovered.rows, "f" * 64)


@pytest.mark.parametrize(
    "field,value",
    [
        ("target", "core"),
        ("component_version", "0.1.49"),
        ("source_manifest_sha256", "0" * 64),
        ("binary_sha256", "0" * 64),
        ("execution_qualified", True),
        ("schema", "fabricated-build-receipt"),
    ],
)
def test_even_resealed_fixture_requires_correct_native_contract(
    recovered, field, value
):
    name = "Contents/Windows/BuildEvidence/gui.json"
    data = json.loads(recovered.blobs[name])
    data[field] = value
    recovered.blobs[name] = encode(data)
    recovered.repin()
    with pytest.raises(ValueError, match="evidence contract"):
        bundle.accepted_native_package(recovered.root, recovered.rows, "f" * 64)


@pytest.mark.parametrize("field", ["files", "sdk_files"])
@pytest.mark.parametrize("mutation", ["traversal", "duplicate_case", "size", "sha"])
def test_source_and_sdk_rows_have_strict_paths_and_identities(
    recovered, field, mutation
):
    name = "Contents/Windows/BuildEvidence/gui.json"
    data = json.loads(recovered.blobs[name])
    row = data[field][0]
    if mutation == "traversal":
        row["path"] = "../foreign"
    elif mutation == "duplicate_case":
        data[field].append({**row, "path": row["path"].upper()})
    elif mutation == "size":
        row["size"] = True
    else:
        row["sha256"] = "bad"
    recovered.blobs[name] = encode(data)
    recovered.repin()
    with pytest.raises(ValueError):
        bundle.accepted_native_package(recovered.root, recovered.rows, "f" * 64)


def test_leaf_symlink_refused(recovered, tmp_path):
    file = recovered.root / "Contents/Windows/producer.json"
    outside = tmp_path / "producer.json"
    outside.write_bytes(file.read_bytes())
    file.unlink()
    try:
        file.symlink_to(outside)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege unavailable")
        raise
    with pytest.raises(ValueError):
        bundle.accepted_native_package(recovered.root, recovered.rows, "f" * 64)


def test_reparse_attribute_refused(recovered, monkeypatch):
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == recovered.root / "Contents/Windows/producer.json":
            return SimpleNamespace(
                st_mode=stat.S_IFREG, st_size=info.st_size, st_file_attributes=0x400
            )
        return info

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError):
        bundle.accepted_native_package(recovered.root, recovered.rows, "f" * 64)


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--gui", "a"],
        ["--worker", "b"],
        ["--accepted-native-package", "kit", "--gui", "a", "--worker", "b"],
    ],
)
def test_cli_rejects_missing_or_mixed_native_modes(monkeypatch, extra):
    arguments = ["windows_bundle", "assemble"]
    for name in [
        "source-manifest",
        "host",
        "runtime",
        "web",
        "crt",
        "webview-installer",
        "webview-receipt",
        "output",
        "autocad",
    ]:
        arguments += ["--" + name, "unused"]
    monkeypatch.setattr(sys, "argv", arguments + extra)
    with pytest.raises(SystemExit) as error:
        bundle.main()
    assert error.value.code == 2


def test_original_mode_still_requires_original_receipts(tmp_path):
    binary = tmp_path / "old/package/GreenAtlasBridge.arx"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"native")
    with pytest.raises(FileNotFoundError):
        bundle.native_evidence(binary, {}, "f" * 64)


@pytest.mark.parametrize("path", [
    "tools/autocad-bridge/native/new_header.h",
    "tools/autocad-bridge/query-worker/NEW_HEADER.HPP",
    "tools/autocad-bridge/vendor/sub/readme.MD",
    "tools/autocad-bridge/windows/sub/license.MIT",
])
def test_new_native_inventory_member_in_current_seal_refuses_reuse(recovered, path):
    recovered.rows[path] = SimpleNamespace(bytes=4, sha256=sha(b"new!"))
    with pytest.raises(ValueError, match="closure pathset"):
        bundle.accepted_native_package(recovered.root, recovered.rows, "f" * 64)
