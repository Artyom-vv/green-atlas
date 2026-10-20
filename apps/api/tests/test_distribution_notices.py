"""Уведомления собираются из синтетических metadata, без запуска пакетов."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/desktop/distribution_notices.py"
SPEC = importlib.util.spec_from_file_location("distribution_notices_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
notices = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notices)


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    repository = tmp_path / "repo"
    store = repository / "node_modules/.pnpm"
    store.mkdir(parents=True)
    (store / "lock.yaml").write_text("lockfileVersion: 9\n")
    venv = tmp_path / "venv"
    packages = venv / "site-packages"
    packages.mkdir(parents=True)
    monkeypatch.setattr(notices, "_python_sites", lambda: [(packages, venv)])
    interpreter = tmp_path / "interpreter"
    interpreter.mkdir()
    monkeypatch.setattr(notices, "sys", SimpleNamespace(base_prefix=str(interpreter), platform="darwin", implementation=SimpleNamespace(name="cpython"), version="3.13.15 fixture"))
    return repository, packages, store


def python_package(packages: Path, extra: str = "") -> Path:
    metadata = packages / "example-1.2.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: example\nVersion: 1.2\n"
        "License-Expression: BSD-3-Clause\nAuthor: Иван Example\n" + extra + "\n",
        encoding="utf-8",
    )
    return metadata


def node_package(store: Path, name: str = "@example/tool") -> Path:
    package = store / "tool@1.0/node_modules" / name
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({
        "name": name, "version": "1.0", "license": "MIT", "author": {"name": "Author"},
        "scripts": {"postinstall": "THIS MUST NEVER EXECUTE"},
    }))
    return package


def test_exact_declared_and_plain_license_text_metadata_and_namespace(environment):
    repository, packages, store = environment
    metadata = python_package(packages, "License-File: LICENSE.txt\n")
    license_file = metadata / "licenses/LICENSE.txt"
    license_file.parent.mkdir()
    license_file.write_bytes(b"Python license\nCopyright Example\n")
    module = packages / "example"
    module.mkdir()
    (module / "__init__.py").write_text("raise RuntimeError('do not import')")
    (module / "NOTICE").write_text("Package notice\n")
    (metadata / "RECORD").write_text("example/__init__.py,,\n../../../bin/example,,\n")
    node = node_package(store)
    (node / "LICENSE-MIT").write_text("Node license\n")
    output = repository / "dist/Notices/Dependencies"
    summary = notices.collect_notices(output, repository)
    assert summary["python_packages"] == summary["node_packages"] == 1
    assert summary["license_files"] == 3
    assert len(summary["missing_license_files"]) == 1
    assert summary["missing_license_files"][0]["ecosystem"] == "python-interpreter"
    index = json.loads((output / summary["manifest"]).read_bytes())
    python = next(p for p in index["packages"] if p["ecosystem"] == "python")
    assert python["metadata"]["License-Expression"] == ["BSD-3-Clause"]
    assert python["metadata"]["Author"] == ["Иван Example"]
    assert sorted((output / row["path"]).read_text() for p in index["packages"] for row in p["license_files"]) == [
        "Node license\n", "Package notice\n", "Python license\nCopyright Example\n",
    ]
    assert not list(output.rglob("*.py"))
    assert str(packages) not in json.dumps(index)


def test_missing_text_preserves_exact_metadata_and_explicit_fallback(environment):
    repository, packages, store = environment
    python_package(packages, "License: custom field only\nLicense-File: missing.txt\n")
    node_package(store)
    summary = notices.collect_notices(repository / "out", repository)
    assert summary["license_files"] == 0
    assert len(summary["missing_license_files"]) == 3
    index = json.loads((repository / "out/third-party-notices.json").read_bytes())
    assert all(p["license_file_status"] == "metadata_only" for p in index["packages"])
    python = next(p for p in index["packages"] if p["ecosystem"] == "python")
    assert python["metadata"]["License"] == ["custom field only"]
    assert python["missing_declared_license_files"] == ["missing.txt"]


@pytest.mark.parametrize("path", ["../private.txt", "/private.txt", "C:/private.txt", "..\\private.txt", "licenses//LICENSE"])
def test_declared_license_traversal_rejected_before_output(environment, path):
    repository, packages, _ = environment
    python_package(packages, "License-File: " + path + "\n")
    with pytest.raises(ValueError, match="License-File"):
        notices.collect_notices(repository / "out", repository)
    assert not (repository / "out").exists()


def test_node_license_symlink_escape_rejected(environment, tmp_path):
    repository, _, store = environment
    node = node_package(store)
    external = tmp_path / "private.txt"
    external.write_text("must not copy")
    (node / "LICENSE").symlink_to(external)
    with pytest.raises(ValueError, match="Ссылка"):
        notices.collect_notices(repository / "out", repository)
    assert not (repository / "out").exists()


def test_node_dependency_links_do_not_duplicate_the_owner(environment):
    repository, _, store = environment
    package = node_package(store, "tool")
    (package / "LICENSE").write_text("license")
    links = store / "other@1.0/node_modules"
    links.mkdir(parents=True)
    (links / "tool").symlink_to(package, target_is_directory=True)
    assert notices.collect_notices(repository / "out", repository)["node_packages"] == 1


def test_python_license_symlink_escape_rejected(environment, tmp_path):
    repository, packages, _ = environment
    metadata = python_package(packages)
    external = tmp_path / "private.txt"
    external.write_text("must not copy")
    (metadata / "LICENSE").symlink_to(external)
    with pytest.raises(ValueError, match="Ссылка"):
        notices.collect_notices(repository / "out", repository)
    assert not (repository / "out").exists()


def test_declared_license_cannot_copy_python_source(environment):
    repository, packages, _ = environment
    metadata = python_package(packages, "License-File: source.py\n")
    (metadata / "source.py").write_text("raise RuntimeError('source')")
    with pytest.raises(ValueError, match="код или бинарник"):
        notices.collect_notices(repository / "out", repository)
    assert not (repository / "out").exists()


def test_output_symlink_cannot_escape_checkout(environment, tmp_path):
    repository, _, _ = environment
    external = tmp_path / "outside"
    external.mkdir()
    (repository / "out").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="Ссылка"):
        notices.collect_notices(repository / "out", repository)
    assert list(external.iterdir()) == []


def test_interpreter_notice_is_exact_and_required_on_windows(environment):
    repository, _, _ = environment
    notices.sys.platform = "win32"
    with pytest.raises(ValueError, match="лицензия CPython"):
        notices.collect_notices(repository / "missing", repository)
    license_file = Path(notices.sys.base_prefix) / "LICENSE.txt"
    license_file.write_text("CPython copyright and license\n")
    summary = notices.collect_notices(repository / "out", repository)
    assert summary["interpreter_license_files"] == 1
    index = json.loads((repository / "out/third-party-notices.json").read_bytes())
    interpreter = next(p for p in index["packages"] if p["ecosystem"] == "python-interpreter")
    assert interpreter["version"] == "3.13.15"
    assert (repository / "out" / interpreter["license_files"][0]["path"]).read_bytes() == license_file.read_bytes()


@pytest.mark.parametrize("content", [b"binary\0text", b"\xffinvalid-utf8"])
def test_non_text_notices_rejected(environment, content):
    repository, packages, _ = environment
    metadata = python_package(packages)
    (metadata / "LICENSE").write_bytes(content)
    with pytest.raises(ValueError):
        notices.collect_notices(repository / "out", repository)
    assert not (repository / "out").exists()


def test_file_budget_and_no_overwrite(environment, monkeypatch):
    repository, _, store = environment
    package = node_package(store, "tool")
    (package / "LICENSE").write_text("license")
    output = repository / "out"
    notices.collect_notices(output, repository)
    original = (output / "third-party-notices.json").read_bytes()
    with pytest.raises(ValueError, match="уже существуют"):
        notices.collect_notices(output, repository)
    assert (output / "third-party-notices.json").read_bytes() == original
    monkeypatch.setattr(notices, "MAX_FILE_BYTES", 4)
    with pytest.raises(ValueError, match="ограниченным"):
        notices.collect_notices(repository / "too-large", repository)
    assert not (repository / "too-large").exists()


def test_mac_standalone_interpreter_license_stays_under_base_prefix(environment):
    repository, _, _ = environment
    license_file = Path(notices.sys.base_prefix) / "lib/python3.13/LICENSE.txt"
    license_file.parent.mkdir(parents=True)
    license_file.write_bytes(b"CPython standalone license\n")
    summary = notices.collect_notices(repository / "out", repository)
    assert summary["interpreter_license_files"] == 1
    index = json.loads((repository / "out/third-party-notices.json").read_bytes())
    interpreter = next(p for p in index["packages"] if p["ecosystem"] == "python-interpreter")
    assert interpreter["license_files"][0]["source"] == "lib/python3.13/LICENSE.txt"


def explicit_store(repository: Path, root: Path) -> Path:
    store = root / "node_modules/.pnpm"
    store.mkdir(parents=True)
    lock = b"lockfileVersion: '9.0'\n\npackages:\n\n  '@example/tool@1.0':\n    resolution: {integrity: fixture}\n\nsnapshots:\n"
    (repository / "pnpm-lock.yaml").write_bytes(lock)
    (store / "lock.yaml").write_bytes(lock)
    return store


def test_explicit_physical_store_requires_same_lock_and_actual_package_versions(environment, tmp_path):
    repository, _, _ = environment
    store = explicit_store(repository, tmp_path / "shared")
    package = node_package(store)
    (package / "LICENSE").write_text("Shared dependency notice\n")
    summary = notices.collect_notices(repository / "out", repository, node_store=store)
    assert summary["node_packages"] == summary["license_files"] == 1
    index = json.loads((repository / "out/third-party-notices.json").read_bytes())
    assert index["explicit_node_store"]["package_versions_verified"] is True
    node = next(p for p in index["packages"] if p["ecosystem"] == "node")
    assert node["location"].startswith("pnpm-store/")
    assert str(store) not in json.dumps(index)
    metadata = json.loads((package / "package.json").read_bytes())
    metadata["version"] = "9.9"
    (package / "package.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="name/version"):
        notices.collect_notices(repository / "wrong-version", repository, node_store=store)
    assert not (repository / "wrong-version").exists()


def test_explicit_store_lock_mismatch_rejected_before_output(environment, tmp_path):
    repository, _, _ = environment
    store = explicit_store(repository, tmp_path / "shared")
    (store / "lock.yaml").write_bytes(b"different lock\n")
    with pytest.raises(ValueError, match="не соответствует"):
        notices.collect_notices(repository / "out", repository, node_store=store)
    assert not (repository / "out").exists()


def test_explicit_store_alias_and_nested_escape_rejected(environment, tmp_path):
    repository, _, _ = environment
    store = explicit_store(repository, tmp_path / "shared")
    alias = tmp_path / "alias"
    alias.symlink_to(store.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="каноническим"):
        notices.collect_notices(repository / "alias-out", repository, node_store=alias / ".pnpm")
    package = node_package(store)
    external = tmp_path / "private.txt"
    external.write_text("must not copy")
    (package / "LICENSE").symlink_to(external)
    with pytest.raises(ValueError, match="Ссылка"):
        notices.collect_notices(repository / "escape-out", repository, node_store=store)
    assert not (repository / "escape-out").exists()
