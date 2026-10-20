"""Small real-Git fixtures for an immutable handoff derivation, without secrets."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from scripts.desktop import repair_windows133_handoff as repair


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, env=repair.clean_git_environment(),
                          check=True, capture_output=True).stdout.decode().strip()


@pytest.fixture
def kit(tmp_path, monkeypatch):
    dev = tmp_path / "clean dev"
    dev.mkdir()
    git(dev, "init")
    git(dev, "config", "user.email", "fixture@example.invalid")
    git(dev, "config", "user.name", "Fixture")
    git(dev, "config", "core.autocrlf", "false")
    base_files = {
        "apps/api/app/__init__.py": b"",
        "apps/api/app/distribution/preflight.py": b'RUNTIME_RESOURCES = ("resource.json",)\n',
        "apps/api/app/resource.json": b'{}\n',
        "tools/autocad-bridge/desktop/entry.py": b"# fixture entry\n",
        "apps/api/tests/test_windows_installer_access.py": b"# previous fixture\n",
    }
    for name, data in base_files.items():
        path = dev / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    seal_script = Path(__file__).resolve().parents[3] / "scripts/desktop/seal_git_source.py"
    (dev / "scripts/desktop").mkdir(parents=True)
    shutil.copyfile(seal_script, dev / "scripts/desktop/seal_git_source.py")
    git(dev, "add", ".")
    git(dev, "commit", "-m", "synthetic base")
    base = git(dev, "rev-parse", "HEAD")
    tree = git(dev, "rev-parse", "HEAD^{tree}")
    base_rows = repair.snapshot(dev, base)
    original = tmp_path / "old kit"
    original.mkdir()
    for row in base_rows:
        target = original / "source" / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dev / row["path"], target)
    dump(original / "source-manifest.json", {"files": [
        {key: row[key] for key in ("path", "sha256", "bytes")} for row in base_rows
    ]})
    seal = repair.file_sha(original / "source-manifest.json")
    source_export = {"git_commit": base, "git_tree": tree, "source_manifest_sha256": seal,
                     "full_tracked_tree": True, "source_transforms": False, "files": base_rows}
    dump(original / "SOURCE-EXPORT.json", source_export)
    (original / "SOURCE-COMMIT.raw").write_bytes(subprocess.check_output(
        ["git", "cat-file", "commit", base], cwd=dev))
    dump(original / "git-source-receipt.json", {"git_commit": base})
    text = "Verify-InputCache.py\n"
    text += "if ($signed.Status -ne 'Valid' -or $signed.SignerCertificate.Subject -notmatch 'Pyrsys'" + repair.FRAGMENTS[0] + ") {}\n"
    text += "if ($signed.Status -ne 'Valid' -or $signed.SignerCertificate.Subject -notmatch 'Pyrsys'" + repair.FRAGMENTS[1] + ") {}\n"
    (original / "Prepare-Inputs.ps1").write_bytes(text.encode("utf-8"))
    for name in ("MAC-SOURCE-QA.json", "HANDOFF-CHECKS.json"):
        dump(original / name, {"scope": "original fixture", "git_commit": base})
    (original / "START-HERE.md").write_text("Original fixture instructions", encoding="utf-8")
    (original / "ACCEPTANCE.md").write_text("Physical tests NOT RUN", encoding="utf-8")
    (original / "Build-Windows133.ps1").write_text("# original immutable entry", encoding="utf-8")
    # Only the new small tool is under test; independent assertions below check
    # the regenerated manifest and real clone instead of trusting these shims.
    for name in ("Verify-Handoff.py", "Restore-SourceGit.py"):
        (original / name).write_text('print("synthetic downstream helper")\n', encoding="utf-8")
    for name in ("build-inputs/vendor.bin", "native131/native.bin", "private/Services.envelope.json"):
        path = original / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"immutable synthetic bytes")
    manifest_rows = [{"path": path.relative_to(original).as_posix(), "bytes": path.stat().st_size,
                      "sha256": repair.file_sha(path)} for path in original.rglob("*") if path.is_file()]
    dump(original / "HANDOFF-FILES.json", {"files": sorted(manifest_rows, key=lambda row: row["path"])})
    monkeypatch.setattr(repair, "BASE_COMMIT", base)
    monkeypatch.setattr(repair, "BASE_TREE", tree)
    monkeypatch.setattr(repair, "BASE_SEAL", seal)
    monkeypatch.setattr(repair, "BASE_MANIFEST", repair.file_sha(original / "HANDOFF-FILES.json"))
    monkeypatch.setattr(repair, "BASE_PREPARE", repair.sha(text.encode()))
    repaired = text
    for fragment in repair.FRAGMENTS:
        repaired = repaired.replace(fragment, "", 1)
    monkeypatch.setattr(repair, "REPAIRED_PREPARE", repair.sha(repaired.encode()))
    (dev / "apps/api/tests/test_windows_installer_access.py").write_bytes(b"# child CWD fixture\n")
    (dev / repair.REPAIR_TOOL).write_bytes(b"# new versioned repair fixture\n")
    git(dev, "add", ".")
    git(dev, "commit", "-m", "synthetic source-only correction")
    return original, dev, git(dev, "rev-parse", "HEAD"), tmp_path / "derived kit"


def test_derivation_uses_real_commit_preserves_base_and_shares_inputs(kit):
    original, dev, commit, output = kit
    before = {path.relative_to(original).as_posix(): repair.file_sha(path)
              for path in original.rglob("*") if path.is_file()}
    result = repair.derive(original, dev, commit, output)
    assert result["original_handoff_modified"] is False
    assert result["unlock_read_or_copied"] is False
    assert git(output / "source", "rev-parse", "HEAD") == commit
    assert (output / "source/.git/shallow").read_text().strip() == commit
    assert git(output / "source", "remote", "get-url", "origin") == "https://github.com/Artyom-vv/green-atlas-dev.git"
    marker = json.loads((output / "source/.git/green-atlas-private-export.json").read_text())
    assert marker["git_commit"] == commit
    assert repair.file_sha(output / "audit/original/HANDOFF-FILES.json") == repair.BASE_MANIFEST
    assert repair.file_sha(output / "Prepare-Inputs.ps1") == repair.REPAIRED_PREPARE
    assert (original / "build-inputs/vendor.bin").stat().st_ino == (output / "build-inputs/vendor.bin").stat().st_ino
    current = json.loads((output / "HANDOFF-FILES.json").read_text())
    for row in current["files"]:
        path = output / row["path"]
        assert path.stat().st_size == row["bytes"] and repair.file_sha(path) == row["sha256"]
    after = {path.relative_to(original).as_posix(): repair.file_sha(path)
             for path in original.rglob("*") if path.is_file()}
    assert before == after
    assert json.loads((output / "SOURCE-EXPORT.json").read_text())["git_commit"] == commit
    assert json.loads((output / "MAC-SOURCE-QA.json").read_text())["state"] == "derived-no-new-test-run-claimed"


def test_verify_only_creates_no_output(kit):
    original, dev, commit, output = kit
    assert repair.derive(original, dev, commit, None)["state"] == "verified-no-output-created"
    assert not output.exists()


@pytest.mark.parametrize("name", ["HANDOFF-FILES.json", "Prepare-Inputs.ps1", "build-inputs/vendor.bin",
                                 "source/apps/api/app/resource.json", "SOURCE-EXPORT.json"])
def test_original_corruption_refused_before_writes(kit, name):
    original, dev, commit, output = kit
    (original / name).write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        repair.derive(original, dev, commit, output)
    assert not output.exists()


def test_application_change_is_not_allowed(kit):
    original, dev, _, output = kit
    (dev / "apps/api/app/resource.json").write_bytes(b"changed application")
    git(dev, "add", ".")
    git(dev, "commit", "-m", "forbidden application change")
    with pytest.raises(ValueError, match="application/native"):
        repair.derive(original, dev, git(dev, "rev-parse", "HEAD"), output)
    assert not output.exists()


def test_existing_output_is_not_overwritten(kit):
    original, dev, commit, output = kit
    output.mkdir()
    (output / "sentinel").write_text("keep")
    with pytest.raises(ValueError):
        repair.derive(original, dev, commit, output)
    assert (output / "sentinel").read_text() == "keep"


def test_keys_and_runtime_userdata_are_not_read_or_copied(kit, monkeypatch):
    original, dev, commit, output = kit
    forbidden = [original / "GreenAtlas-Windows133-Services.key", original / "source/.runtime/user.sqlite"]
    for path in forbidden:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not an input")
    original_open = Path.open
    def guarded_open(path, *args, **kwargs):
        if path in forbidden:
            raise AssertionError("Attempt to read a key or user data")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    repair.derive(original, dev, commit, output)
    assert not (output / forbidden[0].name).exists()
    assert not (output / "source/.runtime/user.sqlite").exists()


def test_foreign_git_environment_does_not_modify_foreign_index(kit, monkeypatch, tmp_path):
    original, dev, commit, output = kit
    foreign = tmp_path / "foreign-index"
    foreign.write_bytes(b"keep foreign index")
    monkeypatch.setenv("GIT_INDEX_FILE", str(foreign))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "foreign.git"))
    repair.derive(original, dev, commit, output)
    assert foreign.read_bytes() == b"keep foreign index"


def test_extra_prepare_edit_is_not_blessed(kit):
    original, *_ = kit
    data = (original / "Prepare-Inputs.ps1").read_bytes()
    with pytest.raises(ValueError):
        repair.patch_prepare(data + b"# unrelated edit")
    changed = repair.patch_prepare(data).decode()
    assert "Verify-InputCache.py" in changed
    assert changed.count("$signed.Status -ne 'Valid'") == 2
    assert changed.count("-notmatch 'Pyrsys'") == 2
    assert "ProductVersion" not in changed


def test_hardlink_failure_requires_explicit_copy_option(kit, monkeypatch):
    original, dev, commit, output = kit
    def fail(*args):
        raise OSError("different volume")
    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(ValueError, match="same volume"):
        repair.derive(original, dev, commit, output)
    assert not output.exists()
    result = repair.derive(original, dev, commit, output, copy_inputs=True)
    assert result["shared_inputs"] == "copied"


def test_shallow_source_has_actionable_ancestry_message(kit, tmp_path):
    original, dev, commit, output = kit
    shallow = tmp_path / "shallow source"
    git(tmp_path, "clone", "--no-local", "--depth=1", dev.as_uri(), str(shallow))
    with pytest.raises(ValueError, match="depth=3"):
        repair.derive(original, shallow, commit, output)
    assert not output.exists()


def test_dotdot_output_is_normalized_before_outside_input_check(kit):
    original, dev, commit, _ = kit
    output = original.parent / "not-created" / ".." / original.name / "nested-output"
    with pytest.raises(ValueError, match="outside both inputs"):
        repair.derive(original, dev, commit, output)
    assert not (original / "nested-output").exists()
