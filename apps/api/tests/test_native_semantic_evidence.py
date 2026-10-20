"""Evidence corruption fails closed; labels never become pipe facts."""

import json
from collections.abc import Callable
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.dxf_import.layers.contracts import Layer, LayerKind
from app.native_query.capture.capture_contracts import CaptureReceipt, SessionTransfer
from app.native_query.capture.capture_store import (
    NativeCaptureStore,
    read_capture_receipt,
)
from app.native_query.capture.semantic_evidence import (
    NativeSemantics,
    attach_semantics,
    label_observation,
    read_semantics,
)


def make_capture(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    matrix = [[1, 0, 0, 10], [0, 1, 0, 20], [0, 0, 1, 0], [0, 0, 0, 1]]
    instances = [
        {
            "source_route": f"A/{index:X}",
            "archive_route": f"B/{index:X}",
            "archive_file": "host.dwg",
            "class": kind,
            "unavailable": "",
            "authored_layer": "0",
            "effective_layer": "XREF|Подписи",
            "transform": matrix,
        }
        for index, kind in enumerate(
            ("AcDbText", "AcDbAttribute", "AcDbAttributeDefinition", "AcDbLine"), 1
        )
    ]
    rows = [
        {
            **item,
            "instance_index": index,
            "origin": origin,
            "value": value,
            "tag": "private person field" if index else "",
            "anchor_local": [1, 2, 0],
            "anchor_wcs": [11, 22, 0],
            "invisible": False,
            "status": "captured",
        }
        for index, (item, origin, value) in enumerate(
            zip(
                instances[:3],
                (
                    "text_annotation",
                    "attribute_instance_value",
                    "attribute_definition_default",
                ),
                ("d=2х500 2400х1450БД", "Газон", "Газон"),
                strict=True,
            )
        )
    ]
    (root / "instances.json").write_text(json.dumps(instances))
    (root / "host.dwg").write_bytes(b"AutoCAD-owned fixture identity only")
    evidence = {
        "schema": "green-atlas.native-semantics/1",
        "instances_sha256": sha256((root / "instances.json").read_bytes()).hexdigest(),
        "display_sha256": "a" * 64,
        "instance_count": 4,
        "eligible_count": 3,
        "emitted_count": 3,
        "omitted_count": 0,
        "complete": True,
        "rows": rows,
    }
    receipt = {
        "schema": "green-atlas.native-session/3",
        "plugin_version": "0.1.48",
        "entry": "host.dwg",
        "units_code": 6,
        "source_path": "private/source.dwg",
        "display_sha256": "a" * 64,
        "source_dbmod_before": 0,
        "source_dbmod_after": 0,
        "source_disk_hashes_verified": True,
        "instance_count": 4,
        "instances_sha256": evidence["instances_sha256"],
        "files": [
            {
                "path": "host.dwg",
                "sha256": sha256((root / "host.dwg").read_bytes()).hexdigest(),
                "bytes": (root / "host.dwg").stat().st_size,
            }
        ],
        "references": [],
    }
    write_evidence(root, receipt, evidence)
    return receipt, evidence


def write_evidence(
    root: Path, receipt: dict[str, Any], evidence: dict[str, Any]
) -> None:
    content = json.dumps(evidence).encode()
    (root / "semantics.json").write_bytes(content)
    receipt.update(
        semantics_sha256=sha256(content).hexdigest(),
        semantics_bytes=len(content),
        semantics_count=evidence["emitted_count"],
    )
    (root / "session.json").write_text(json.dumps(receipt))


def imported_layer() -> Layer:
    return Layer(
        id="layer-local",
        source_name="XREF|Подписи",
        suggested_kind=LayerKind.IGNORE,
        object_count=4,
        color="#000000",
        entity_types={"TEXT": 1, "ATTRIB": 1, "ATTDEF": 1, "LINE": 1},
    )


def test_exact_native_instances_retain_label_scope_and_ignore_defaults(
    tmp_path: Path,
) -> None:
    make_capture(tmp_path)
    evidence, summaries = read_semantics(
        tmp_path, read_capture_receipt(tmp_path), lambda: None
    )
    assert evidence.complete
    summary = summaries["XREF|Подписи"]
    assert summary.complete and summary.default_count == 1
    assert summary.captured_count == summary.eligible_count == 3
    assert [(item.kind, item.count) for item in summary.observations] == [
        ("dimension_notation", 1),
        ("lawn_label", 1),
    ]
    dimensional = summary.observations[0]
    assert dimensional.section_notation == (2400, 1450)
    assert (
        dimensional.unit == "not_stated"
        and dimensional.measurement_reference == "unknown"
    )
    assert dimensional.physical_object_association == "unknown"
    layer = imported_layer()
    attach_semantics(tmp_path, "a" * 64, [layer])
    assert layer.semantic_evidence == summary
    assert layer.utility_context is None and layer.mapped_kind is None
    assert "private" not in json.dumps(summary.external_features())


@pytest.mark.parametrize(
    "field,value",
    [
        ("instance_index", 3),
        ("source_route", "A/99"),
        ("archive_route", "B/99"),
        ("archive_file", "xref-1.dwg"),
        ("authored_layer", "Trees"),
        ("effective_layer", "Other"),
        ("origin", "attribute_instance_value"),
        ("anchor_wcs", [11, 23, 0]),
        ("class", "AcDbAttribute"),
    ],
)
def test_row_binding_corruption_is_rejected(
    tmp_path: Path, field: str, value: Any
) -> None:
    receipt, evidence = make_capture(tmp_path)
    evidence["rows"][0][field] = value
    write_evidence(tmp_path, receipt, evidence)
    with pytest.raises(ValueError):
        read_semantics(tmp_path, read_capture_receipt(tmp_path), lambda: None)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evidence: evidence.update(display_sha256="b" * 64),
        lambda evidence: evidence.update(instances_sha256="b" * 64),
        lambda evidence: evidence.update(instance_count=5),
        lambda evidence: evidence.update(emitted_count=2),
        lambda evidence: evidence.update(complete=False),
        lambda evidence: evidence["rows"].append(deepcopy(evidence["rows"][0])),
    ],
)
def test_semantic_capture_identity_counts_and_completeness_are_checked(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    receipt, evidence = make_capture(tmp_path)
    mutate(evidence)
    write_evidence(tmp_path, receipt, evidence)
    with pytest.raises(ValueError):
        read_semantics(tmp_path, read_capture_receipt(tmp_path), lambda: None)


def test_file_hash_and_import_source_identity_are_checked(tmp_path: Path) -> None:
    make_capture(tmp_path)
    with pytest.raises(ValueError, match="another display"):
        attach_semantics(tmp_path, "b" * 64, [imported_layer()])
    with (tmp_path / "semantics.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="identity changed"):
        read_semantics(tmp_path, read_capture_receipt(tmp_path), lambda: None)


def test_nonsemantic_instance_index_hash_is_checked_before_attaching_labels(
    tmp_path: Path,
) -> None:
    make_capture(tmp_path)
    content = json.loads((tmp_path / "instances.json").read_bytes())
    content[-1]["effective_layer"] = "Changed physical geometry"
    (tmp_path / "instances.json").write_text(json.dumps(content))
    with pytest.raises(ValueError, match="index identity changed"):
        read_semantics(tmp_path, read_capture_receipt(tmp_path), lambda: None)


def test_repeated_native_insert_labels_keep_distinct_exact_transforms(
    tmp_path: Path,
) -> None:
    receipt, evidence = make_capture(tmp_path)
    instances = json.loads((tmp_path / "instances.json").read_bytes())
    repeated = deepcopy(instances[0])
    repeated.update(
        source_route="C/1", archive_route="D/1", effective_layer="Other instance"
    )
    repeated["transform"][0][3] = 100
    instances.append(repeated)
    (tmp_path / "instances.json").write_text(json.dumps(instances))
    row = deepcopy(evidence["rows"][0])
    row.update(**repeated, instance_index=4, value="Грунт", anchor_wcs=[101, 22, 0])
    evidence["rows"].append(row)
    evidence.update(
        instance_count=5,
        emitted_count=4,
        eligible_count=4,
        instances_sha256=sha256((tmp_path / "instances.json").read_bytes()).hexdigest(),
    )
    receipt.update(instance_count=5, instances_sha256=evidence["instances_sha256"])
    write_evidence(tmp_path, receipt, evidence)
    _, summaries = read_semantics(
        tmp_path, read_capture_receipt(tmp_path), lambda: None
    )
    assert summaries["Other instance"].observations[0].kind == "soil_label"
    assert len(summaries["XREF|Подписи"].observations) == 2
    row["anchor_wcs"] = [11, 22, 0]
    write_evidence(tmp_path, receipt, evidence)
    with pytest.raises(ValueError, match="exact source instance"):
        read_semantics(tmp_path, read_capture_receipt(tmp_path), lambda: None)


def test_omitted_and_failed_rows_remain_explicit_without_values(tmp_path: Path) -> None:
    receipt, evidence = make_capture(tmp_path)
    evidence["rows"].pop()
    evidence.update(emitted_count=2, omitted_count=1, complete=False)
    evidence["rows"][0].update(
        status="value_limit", value="", tag="", anchor_local=None, anchor_wcs=None
    )
    write_evidence(tmp_path, receipt, evidence)
    _, summaries = read_semantics(
        tmp_path, read_capture_receipt(tmp_path), lambda: None
    )
    summary = summaries["XREF|Подписи"]
    assert (
        not summary.complete
        and summary.omitted_count == 1
        and summary.captured_count == 1
    )
    assert [observation.kind for observation in summary.observations] == ["lawn_label"]
    evidence["rows"][0]["value"] = "Газон"
    with pytest.raises(ValidationError):
        NativeSemantics.model_validate(evidence)


@pytest.mark.parametrize(
    "text",
    [
        "Иванов",
        "Иванов Газон",
        "улица Газон 12",
        "Иванов d=400ст.",
        "d=400; execute",
        "149.02в.тр.",
        "ПОЖ.",
        "d=0",
        "d=-400",
        "d=2х500 0х1450",
        "DN0000",
        "Газон" + "x" * 300,
    ],
)
def test_complete_label_allowlist_excludes_personal_and_opaque_text(text: str) -> None:
    assert label_observation(text, "text_annotation") is None


def test_nominal_designation_is_not_an_outer_diameter_or_network_fact() -> None:
    observation = label_observation("DN100", "attribute_instance_value")
    assert observation and observation.kind == "nominal_designation_note"
    assert (
        observation.measurement_reference == "unknown"
        and observation.physical_object_association == "unknown"
    )
    assert label_observation("DN100", "attribute_definition_default") is None


def test_capture_version_requires_all_semantic_identity_fields(tmp_path: Path) -> None:
    receipt, _ = make_capture(tmp_path)
    for field in ("semantics_bytes", "semantics_sha256", "semantics_count"):
        invalid = {key: value for key, value in receipt.items() if key != field}
        with pytest.raises(ValidationError):
            CaptureReceipt.model_validate(invalid)
    receipt.update(schema="green-atlas.native-session/2", plugin_version="0.1.47")
    with pytest.raises(ValidationError):
        CaptureReceipt.model_validate(receipt)


def test_capture_store_admits_and_atomically_retains_semantics(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    native = staging / "Native"
    make_capture(native)
    manifest = SessionTransfer.model_validate(
        {
            "entry": "Native/session.json",
            "files": [
                {
                    "name": "Native/" + path.name,
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path.read_bytes()).hexdigest(),
                }
                for path in native.iterdir()
            ],
        }
    )
    store = NativeCaptureStore(tmp_path / "captures")
    target = store.retain(
        "00000000-0000-0000-0000-000000000001",
        "a" * 64,
        "0.1.48",
        staging,
        staging,
        manifest,
        lambda: None,
    )
    assert (target / "semantics.json").read_bytes() == (
        native / "semantics.json"
    ).read_bytes()
    assert store.package("00000000-0000-0000-0000-000000000001", "a" * 64)
