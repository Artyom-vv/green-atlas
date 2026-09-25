import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cad_dependencies import (
    ROOT,
    inventory,
    legacy_edges,
    load_baseline,
    python_edges,
    violations,
)


def test_repository_cannot_add_legacy_import_edges():
    assert not violations(inventory(ROOT), load_baseline())


def test_new_reader_cannot_hide_in_relative_or_member_import():
    for source in (
        "from . import adapters",
        "from app.dxf_import import adapters",
        "from .adapters import EzdxfReader",
    ):
        edges = python_edges(source, "app.dxf_import.new_reader", "new_reader.py")
        assert violations(edges, set())


def test_literal_dynamic_legacy_import_is_detected():
    edges = python_edges(
        'importlib.import_module("ezdxf")', "app.example", "example.py"
    )
    assert violations(edges, set())


def test_reader_dispatch_without_import_is_detected():
    edges = python_edges("self.dxf_reader.read(name, content)", "app.new", "new.py")
    assert violations(edges, set())


def test_new_entrypoint_in_existing_module_is_not_auto_approved():
    old = python_edges("def old():\n app.import_dxf()", "app.api", "api.py")
    new = python_edges("def new():\n app.import_dxf()", "app.api", "api.py")
    assert violations(old + new, legacy_edges(old))


def test_new_symbols_in_existing_legacy_module_are_not_auto_approved():
    old = python_edges("from ezdxf import read", "app.old", "old.py")
    new = python_edges("from ezdxf import read, new_parser", "app.old", "old.py")
    assert violations(new, legacy_edges(old))


def test_protected_geometry_cannot_use_a_baseline_exception():
    edges = python_edges("import ezdxf", "app.geometry.new", "new.py")
    assert violations(edges, legacy_edges(edges))


def test_baseline_allows_debt_removal_not_new_debt():
    edges = python_edges("import ezdxf", "app.old", "old.py")
    assert not violations([], legacy_edges(edges))
    assert not violations(edges, legacy_edges(edges))
    new = python_edges("import ezdxf", "app.new", "new.py")
    assert violations(edges + new, legacy_edges(edges))
