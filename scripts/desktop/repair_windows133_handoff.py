"""Derive a repaired133 kit locally; never edit the immutable ZIP or read its key.

Run from the new clean dev checkout with the old qualified Python. Vendor/native
inputs are hardlinked on the same volume, while source is a real shallow clone.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

BASE_COMMIT = "b02a38d64cbfa7ead3ccd40bf4ecfbbdfba7b37c"
BASE_TREE = "e5bb7ed2e4c458d4bf4444905b03c9cdf49d3f70"
BASE_SEAL = "f242d6635674887aa7e33c2fe476b9dd381a4fef53398c94f32a33a033009604"
BASE_MANIFEST = "54bb8073abaf075525befa588126c3e7549579e343126e98742f07f7553400c1"
BASE_PREPARE = "058fc5f0f1b2b3ff747582bd9641668710130ad3c7e8fb5cf106d215dc7e9c66"
REPAIRED_PREPARE = "227234200db3d63cd33b4660f88b7c780d81b25069981e6f264f581567ef826f"
REPAIR_TOOL = "scripts/desktop/repair_windows133_handoff.py"
REPAIR_TEST = "apps/api/tests/test_windows_handoff_repair.py"
SOURCE_CHANGES = {REPAIR_TOOL, REPAIR_TEST, "apps/api/tests/test_windows_installer_access.py",
                  "docs/obsidian/03 Модули/Подготовка Windows и упаковка.md"}
FRAGMENTS = (
    " -or (Get-Item -LiteralPath $existing.iscc).VersionInfo.ProductVersion -notmatch '^6\\.7\\.3($|[ .])'",
    " -or (Get-Item -LiteralPath (Join-Path $innoRoot 'ISCC.exe')).VersionInfo.ProductVersion -notmatch '^6\\.7\\.3($|[ .])'",
)
MUTABLE = {
    "Prepare-Inputs.ps1", "HANDOFF-FILES.json", "SOURCE-EXPORT.json",
    "source-manifest.json", "git-source-receipt.json", "SOURCE-COMMIT.raw",
    "MAC-SOURCE-QA.json", "HANDOFF-CHECKS.json", "START-HERE.md",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def plain(path: Path) -> Path:
    for current in (path, *path.parents):
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Input contains a link or reparse point")
    return path


def relative(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(ord(char) < 32 for char in value)):
        raise ValueError("Unsafe input path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", "..", ""} for part in value.split("/")):
        raise ValueError("Unsafe input path")
    return value


def load(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("Expected metadata object")  # noqa: TRY004
    return value


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_git_environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    env.pop("GITHUB_SHA", None)
    return env


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, env=clean_git_environment(),
                          check=True, capture_output=True).stdout


def patch_prepare(data: bytes) -> bytes:
    if sha(data) != BASE_PREPARE:
        raise ValueError("Original Prepare-Inputs identity differs; no repair applied")
    text = data.decode("utf-8")
    for fragment in FRAGMENTS:
        if text.count(fragment) != 1:
            raise ValueError("Expected exactly two known PE-version conjuncts")
        text = text.replace(fragment, "", 1)
    repaired = text.encode("utf-8")
    if sha(repaired) != REPAIRED_PREPARE:
        raise ValueError("Repair changed more than the two approved conjuncts")
    return repaired


def verify_original(root: Path) -> tuple[list[dict], dict, bytes]:
    plain(root)
    manifest_bytes = plain(root / "HANDOFF-FILES.json").read_bytes()
    if sha(manifest_bytes) != BASE_MANIFEST:
        raise ValueError("Not the immutable accepted133 base handoff manifest")
    rows = load(manifest_bytes)["files"]
    seen = set()
    for row in rows:
        name = relative(row["path"])
        if name.casefold() in seen or name.lower().endswith(".key"):
            raise ValueError("Repeated path or private unlock in immutable manifest")
        seen.add(name.casefold())
        path = plain(root / name)
        if not path.is_file() or path.stat().st_size != row["bytes"] or file_sha(path) != row["sha256"]:
            raise ValueError("Original handoff file mismatch: " + name)
    source = load((root / "SOURCE-EXPORT.json").read_bytes())
    if (source.get("git_commit") != BASE_COMMIT or source.get("git_tree") != BASE_TREE
            or source.get("source_manifest_sha256") != BASE_SEAL
            or sha((root / "source-manifest.json").read_bytes()) != BASE_SEAL
            or source.get("full_tracked_tree") is not True or source.get("source_transforms") is not False):
        raise ValueError("Original canonical source identity differs")
    metadata = root / "source/.git"
    if os.path.lexists(metadata):
        plain(metadata)
        marker = plain(metadata / "green-atlas-private-export.json")
        if (not metadata.is_dir() or load(marker.read_bytes()).get("git_commit") != BASE_COMMIT
                or git(root / "source", "rev-parse", "HEAD").decode().strip() != BASE_COMMIT
                or git(root / "source", "status", "--porcelain=v1", "--untracked-files=no")):
            raise ValueError("Original source Git is not the unchanged owned base; no reset performed")
    return rows, source, patch_prepare((root / "Prepare-Inputs.ps1").read_bytes())


def snapshot(root: Path, commit: str) -> list[dict]:
    plain(root)
    if (git(root, "rev-parse", "HEAD").decode().strip() != commit
            or Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).absolute() != root
            or git(root, "status", "--porcelain=v1", "--untracked-files=no")):
        raise ValueError("New source must be a separate clean checkout at the specified real commit")
    if git(root, "rev-parse", "--show-object-format").strip() != b"sha1":
        raise ValueError("Expected the canonical SHA1 Git repository")
    result = []
    for record in git(root, "ls-tree", "-r", "-z", commit).split(b"\0"):
        if not record:
            continue
        description, encoded = record.split(b"\t", 1)
        mode, kind, blob = description.decode().split()
        name = relative(encoded.decode())
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError("Source snapshot must contain regular tracked files")
        data = plain(root / name).read_bytes()
        if hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != blob:
            raise ValueError("New checkout bytes differ from Git blob")
        result.append({"path": name, "bytes": len(data), "sha256": sha(data),
                       "git_blob_sha1": blob, "git_mode": mode})
    return sorted(result, key=lambda row: row["path"])


def source_delta(original: dict, current: list[dict]) -> list[str]:
    old = {row["path"]: row for row in original["files"]}
    new = {row["path"]: row for row in current}
    changed = [name for name in sorted(old.keys() | new.keys())
               if old.get(name) != new.get(name)]
    if any(name not in SOURCE_CHANGES for name in changed):
        raise ValueError("Repair source changed application/native or an unapproved source path")
    if REPAIR_TOOL not in new:
        raise ValueError("The new canonical source must contain this versioned repair tool")
    return changed


def derive(original: Path, source: Path, commit: str, output: Path | None,
           *, copy_inputs: bool = False) -> dict:
    if not re.fullmatch("[0-9a-f]{40}", commit) or commit == BASE_COMMIT:
        raise ValueError("Specify the new canonical commit, not the base133 source")
    original, source = Path(os.path.abspath(original)), Path(os.path.abspath(source))
    rows, old_source, repaired = verify_original(original)
    current = snapshot(source, commit)
    try:
        git(source, "merge-base", "--is-ancestor", BASE_COMMIT, commit)
    except subprocess.CalledProcessError as error:
        raise ValueError(
            "New source checkout lacks verified base ancestry. Fetch the dev branch "
            "with --depth=3 or full history in the separate checkout, then retry; "
            "the original handoff is never reset."
        ) from error
    changed = source_delta(old_source, current)
    result = {"base_handoff_manifest_sha256": BASE_MANIFEST, "base_source_commit": BASE_COMMIT,
              "new_source_commit": commit, "source_changed_paths": changed,
              "repaired_prepare_sha256": sha(repaired), "unlock_read_or_copied": False,
              "original_handoff_modified": False, "Windows_build_performed": False}
    if output is None:
        return {**result, "state": "verified-no-output-created"}
    output = Path(os.path.abspath(output))
    plain(output.parent)
    if output.exists() or output.is_relative_to(original) or output.is_relative_to(source):
        raise ValueError("Output must be a new sibling directory, outside both inputs")
    output.mkdir()
    try:
        audit = output / "audit/original"
        audit.mkdir(parents=True)
        shutil.copyfile(original / "HANDOFF-FILES.json", audit / "HANDOFF-FILES.json")
        for row in rows:
            name = row["path"]
            if name.startswith("source/"):
                if name.removeprefix("source/") in changed:
                    target = audit / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(original / name, target)
                continue
            target = (audit if name in MUTABLE else output) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if name.startswith(("build-inputs/", "native131/")) and not copy_inputs:
                try:
                    os.link(original / name, target)
                except OSError as error:
                    raise ValueError("Use a new output directory on the same volume, or explicitly select --copy-inputs") from error
            else:
                shutil.copyfile(original / name, target)
            if file_sha(target) != row["sha256"]:
                raise ValueError("Original input changed during derivation")
        (output / "Prepare-Inputs.ps1").write_bytes(repaired)
        # Real Git clone: no fabricated objects and no mutations of an existing checkout.
        git(output, "-c", "core.autocrlf=false", "clone", "--no-local", "--depth=1",
            "--single-branch", "--no-checkout", "--config", "core.autocrlf=false",
            "--config", "core.filemode=false", source.as_uri(), "source")
        cloned = output / "source"
        git(cloned, "checkout", "--detach", commit)
        git(cloned, "remote", "set-url", "origin", "https://github.com/Artyom-vv/green-atlas-dev.git")
        if snapshot(cloned, commit) != current:
            raise ValueError("The real cloned source differs from the validated clean source")
        seal = cloned / ".runtime/handoff-repair-seal"
        subprocess.run([sys.executable, str(cloned / "scripts/desktop/seal_git_source.py"),
                        str(cloned), str(seal)], env=clean_git_environment(), check=True, capture_output=True)
        for name in ("source-manifest.json", "git-source-receipt.json"):
            shutil.copyfile(seal / name, output / name)
        source_sha = file_sha(output / "source-manifest.json")
        tree = git(cloned, "rev-parse", "HEAD^{tree}").decode().strip()
        (output / "SOURCE-COMMIT.raw").write_bytes(git(cloned, "cat-file", "commit", commit))
        write(output / "SOURCE-EXPORT.json", {
            "schema": "green-atlas.canonical-windows133-source-export/1", "git_commit": commit,
            "git_tree": tree, "files": current, "full_tracked_tree": True,
            "source_transforms": False, "source_manifest_sha256": source_sha,
            "source_files": len(current), "app_python_files": sum(
                row["path"].startswith("apps/api/app/") and row["path"].endswith(".py") for row in current),
            "product_version": "0.1.133", "Windows_execution_qualified": False,
        })
        write(cloned / ".git/green-atlas-private-export.json",
              {"schema": "green-atlas.private-export-git/1", "git_commit": commit})
        result.update(state="derived-kit-ready-unaccepted", output=str(output), source_manifest_sha256=source_sha,
                      source_files=len(current), prepared_vendor_cache_reused=False,
                      shared_inputs="copied" if copy_inputs else "same-volume-hardlinks")
        write(output / "DERIVATION.json", {"schema": "green-atlas.windows133-handoff-derivation/1", **result})
        for name in ("MAC-SOURCE-QA.json", "HANDOFF-CHECKS.json"):
            write(output / name, {"state": "derived-no-new-test-run-claimed", "source_git_commit": commit,
                                 "source_manifest_sha256": source_sha,
                                 "original_report": "audit/original/" + name, "Windows_build_performed": False})
        (output / "START-HERE.md").write_text(
            "# Repaired Windows133 handoff\n\nCanonical source: " + commit + ".\n\n"
            "The original base kit and ZIP are unchanged. See DERIVATION.json and audit/original/. "
            "Only the two incorrect Inno PE-version conditions were removed; exact SHA/size and Valid/Pyrsys checks remain.\n\n"
            "Run Build-Windows133.ps1 with -ValidateOnly first, then without it. "
            "Pass -ServicesKeyFile pointing to the existing original GreenAtlas-Windows133-Services.key. "
            "This repair does not read, copy or decrypt that key. Vendor/native files are local hardlinks; normal locked preparation runs in this new checkout.\n\n"
            "No installer, native rebuild, new qualification or publication is claimed. Follow ACCEPTANCE.md.\n",
            encoding="utf-8",
        )
        inventory = []
        known = [output / "source" / row["path"] for row in current]
        known += [path for path in output.rglob("*") if path.is_file() and not path.is_relative_to(cloned)]
        for path in sorted(known):
            plain(path)
            inventory.append({"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size,
                              "sha256": file_sha(path)})
        write(output / "HANDOFF-FILES.json", {"schema": "green-atlas.private-windows133-handoff-files/1",
                                              "files": inventory, "unlock_key_transferred_separately": True})
        for helper in ("Verify-Handoff.py", "Restore-SourceGit.py"):
            subprocess.run([sys.executable, str(output / helper)], env=clean_git_environment(),
                           check=True, capture_output=True)
        return result
    except BaseException:
        # Only the brand-new output is ours. Hardlink removal does not mutate old inputs.
        shutil.rmtree(output)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--copy-inputs", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if not args.verify_only and args.output is None:
        parser.error("--output is required unless --verify-only")
    try:
        print(json.dumps(derive(args.handoff, args.source_root, args.source_commit,
                                None if args.verify_only else args.output, copy_inputs=args.copy_inputs), indent=2))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print("Handoff repair stopped: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
