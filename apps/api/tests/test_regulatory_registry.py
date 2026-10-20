"""Проверяет сохранность нормативных оснований при переносе сведений в справочник."""

import json
from hashlib import sha256
from importlib.resources import files
from typing import Any

import pytest
from pydantic import ValidationError

from app.regulations.profiles import requirement_profile
from app.regulations.reference_catalog import ReferenceCatalog
from app.regulations.registry import (
    REGISTRY_REVISION,
    applied_record_ids,
    registry_snapshot,
)


def test_registry_keeps_process_basis_separate_from_machine_checked_rules() -> None:
    snapshot = registry_snapshot(["pp743-3.6.3-building"])

    assert snapshot["revision"] == REGISTRY_REVISION
    assert snapshot["applied_rule_ids"] == [
        "pp1160-permit-service",
        "pp616-compensation-process",
        "pp743-3.6.3-building",
    ]
    registry_records = snapshot["records"]
    assert isinstance(registry_records, list)
    records = {item["id"]: item for item in registry_records}
    assert records["pp743-3.6.3-building"]["coverage"] == "implemented"
    assert records["pp743-3.6.3-building"]["machine_checkable"] is True
    assert records["pp616-compensation-process"]["coverage"] == "partial"
    assert records["pp616-compensation-process"]["machine_checkable"] is False
    assert (
        records["pp616-compensation-process"]["release_gate_machine_checkable"] is True
    )
    assert records["pp1160-permit-service"]["coverage"] == "partial"
    assert records["pp1160-permit-service"]["machine_checkable"] is False


def test_unknown_issue_rule_is_not_silently_declared_as_applied_regulation() -> None:
    assert applied_record_ids(["unknown-rule"]) == [
        "pp1160-permit-service",
        "pp616-compensation-process",
    ]


def _digest(value: object) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def test_reference_extraction_preserves_full_registry_and_requirement_profile() -> None:
    # Полные результаты до переноса, включая порядок, None, редакции и адреса источников.
    assert (
        _digest(
            registry_snapshot(
                [
                    "PP743-3.6.3-building",
                    "sp42-9.6-network-water",
                    "unknown",
                    None,
                ]
            )
        )
        == "e2ce432d1402fb24a801fa1b4d85b4d268cbeb77b7e9518da60c288840b5bfa5"
    )
    assert _digest(requirement_profile().model_dump(mode="json")) == (
        "0b23336dd452056c080fd0eda62f3414e7cb382688daf7f9f29ff8ccbaebe328"
    )


def test_requirement_profile_edit_cannot_change_shared_reference_catalog() -> None:
    edited = requirement_profile()
    edited.references[0].note = "Локальная правка ответа"
    edited.references.clear()
    assert len(requirement_profile().references) == 4
    assert _digest(requirement_profile().model_dump(mode="json")) == (
        "0b23336dd452056c080fd0eda62f3414e7cb382688daf7f9f29ff8ccbaebe328"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("procedural_record_ids", ["unknown-process"]),
        ("network_rule_pack", "another-rule-pack"),
        ("registry_revison", "misspelled-key"),
    ],
)
def test_reference_catalog_rejects_unlinked_basis_and_misspelled_config(
    field: str,
    value: Any,
) -> None:
    data = json.loads(files("app.regulations").joinpath("references.json").read_bytes())
    data[field] = value
    with pytest.raises(ValidationError):
        ReferenceCatalog.model_validate(data)
