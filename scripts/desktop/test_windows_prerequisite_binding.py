"""Раздельные идентичности native frontend и frozen backend не ослабляют seal."""

from copy import deepcopy

import pytest

from scripts.desktop.windows_prerequisite_smoke import verify_setup_binding


def pair():
    setup = {"sha256": "b" * 64, "source_manifest_sha256": "s" * 64, "distribution_manifest_sha256": "d" * 64}
    product = {"sha256": "w" * 64, "backend_sha256": setup["sha256"], "native_wizard": True, "source_manifest_sha256": "s" * 64}
    wizard = {"schema": "green-atlas.windows-native-wizard-build/1", **deepcopy(setup), "sha256": product["sha256"], "backend_sha256": setup["sha256"]}
    return product, setup, wizard


def test_native_wizard_and_legacy_setup_have_distinct_valid_bindings():
    product, setup, wizard = pair()
    verify_setup_binding(product, setup, "s" * 64, "d" * 64, wizard)
    product = {"sha256": setup["sha256"], "source_manifest_sha256": "s" * 64}
    verify_setup_binding(product, setup, "s" * 64, "d" * 64)


@pytest.mark.parametrize("side,field", [("product", "backend_sha256"), ("wizard", "sha256"),
    ("wizard", "backend_sha256"), ("wizard", "distribution_manifest_sha256"),
    ("wizard", "source_manifest_sha256"), ("setup", "source_manifest_sha256"),
    ("setup", "distribution_manifest_sha256")])
def test_wrong_component_or_payload_is_rejected(side, field):
    product, setup, wizard = pair()
    {"product": product, "setup": setup, "wizard": wizard}[side][field] = "x" * 64
    with pytest.raises(ValueError):
        verify_setup_binding(product, setup, "s" * 64, "d" * 64, wizard)


def test_native_cannot_pass_without_its_own_receipt():
    product, setup, _ = pair()
    with pytest.raises(ValueError):
        verify_setup_binding(product, setup, "s" * 64, "d" * 64)
