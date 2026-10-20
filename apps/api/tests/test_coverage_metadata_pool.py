"""Повторные метаданные используют общие строки без изменения допуска CAD."""

from copy import deepcopy

import pytest
from pydantic import TypeAdapter, ValidationError
from test_cad_bridge_compiler import near_closed_building_proposal_probe, valid_probe

from app.cad_bridge import compiler
from app.cad_bridge.contracts import CoverageRecord


def separate(value):
    return (" " + value)[1:]


def record(index, layer="ДВ_ГП_Растительность"):
    return {
        "handle": separate("ABC123"),
        "instance_chain": [f"{index:X}"],
        "entity_type": separate("AcDbBlockReference"),
        "layer": separate(layer),
        "status": "context",
        "method": separate("traverse-block-reference"),
        "reason": separate("Сохранён адресный контекст чертежа"),
    }


def normalize(records, *, owned=False):
    return compiler._normalize_coverage(
        {"coverage": records}, {}, {}, {}, {}, set(), set(), consume_owned=owned
    )


def test_pool_shares_metadata_but_preserves_addresses_and_reasons():
    original = [record(1), record(2)]
    expected = deepcopy(original)
    normalized = normalize(original)
    assert original == expected
    for field in ("layer", "entity_type", "method"):
        assert original[0][field] is not original[1][field]
        assert normalized[0][field] is normalized[1][field]
        assert normalized[0][field] == original[0][field]
    for index, value in enumerate(normalized):
        assert value["identity"]["handle"] is original[index]["handle"]
        assert value["reason"] is original[index]["reason"]
    assert (
        normalized[0]["identity"]["handle"] is not normalized[1]["identity"]["handle"]
    )


def test_pool_capacity_retains_earlier_values_without_accepting_new_ones():
    original = [record(index, f"Слой {index}") for index in range(4200)]
    original += [record(4201, "Слой 0"), record(4202, "Слой 4199")]
    normalized = normalize(original)
    assert normalized[0]["layer"] is normalized[-2]["layer"]
    assert normalized[4199]["layer"] == normalized[-1]["layer"]
    assert normalized[4199]["layer"] is not normalized[-1]["layer"]


def test_long_metadata_and_all_reasons_keep_their_own_objects():
    original = [record(1, "я" * 513), record(2, "я" * 513)]
    original[0]["reason"] = separate("я" * 2000)
    original[1]["reason"] = separate("я" * 2000)
    normalized = normalize(original)
    for index in range(2):
        assert normalized[index]["layer"] is original[index]["layer"]
        assert normalized[index]["reason"] is original[index]["reason"]
    assert normalized[0]["layer"] is not normalized[1]["layer"]


def test_pool_does_not_survive_normalization_call():
    left = normalize([record(1)])[0]
    right = normalize([record(1)])[0]
    assert left["layer"] == right["layer"]
    assert left["layer"] is not right["layer"]


@pytest.mark.parametrize("field", ["layer", "entity_type", "method"])
@pytest.mark.parametrize("invalid", [None, [], {}, 12])
def test_invalid_metadata_reaches_same_typed_validation(field, invalid):
    original = record(1)
    original[field] = invalid
    value = normalize([original])[0]
    assert value[field] is invalid
    with pytest.raises(ValidationError) as caught:
        TypeAdapter(CoverageRecord).validate_python(value)
    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        ((field,), "string_type")
    ]


def test_string_subclasses_do_not_enter_pool():
    class UnhashableString(str):
        __hash__ = None

    original = record(1)
    original["layer"] = UnhashableString("Допустимый текст")
    value = normalize([original])[0]
    assert value["layer"] is original["layer"]
    assert (
        TypeAdapter(CoverageRecord).validate_python(value).layer == "Допустимый текст"
    )


@pytest.mark.parametrize("factory", [valid_probe, near_closed_building_proposal_probe])
def test_owned_normalization_preserves_snapshot_and_public_input(factory):
    public = factory()
    public["capture_mode"] = "live_document"
    original = deepcopy(public)
    args = dict(
        autocad_version="2027",
        target="macos-arm64",
        live_source_sha256="a" * 64,
    )
    expected_payload, expected_tolerance = compiler._normalize_probe_payload(
        public, **args
    )
    expected = compiler._admit_probe_payload(expected_payload, expected_tolerance)
    assert public == original
    owned = deepcopy(public)
    actual_payload, actual_tolerance = compiler._normalize_probe_payload(
        owned, consume_owned=True, **args
    )
    actual = compiler._admit_probe_payload(actual_payload, actual_tolerance)
    assert owned["coverage"] == []
    assert actual.model_dump_json(by_alias=True) == expected.model_dump_json(
        by_alias=True
    )
    assert actual.summary.payload_sha256 == expected.summary.payload_sha256
