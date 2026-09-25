"""Run the no-new-CAD-parser ratchet with the normal backend test suite."""

import importlib.util
import sys
from pathlib import Path


def test_no_new_legacy_cad_dependencies():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts/architecture/cad_dependencies.py"
    spec = importlib.util.spec_from_file_location("ga_cad_dependency_audit", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        assert not module.violations(module.inventory(root), module.load_baseline())
    finally:
        sys.modules.pop(spec.name, None)
