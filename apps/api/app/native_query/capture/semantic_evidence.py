"""Чтение версионированных свидетельств CAD-аннотаций; текст не связывается
автоматически с трубами и параметрами сетей.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Literal

import ijson
from pydantic import Field, FiniteFloat, field_validator, model_validator

from app.desktop.tickets import read_regular
from app.dxf_import.layers.contracts import Layer
from app.dxf_import.layers.semantics import LabelObservation, LayerSemanticEvidence
from app.native_query.capture.capture_contracts import (
    MAX_SEMANTIC_ROWS,
    MAX_SEMANTICS_BYTES,
    CaptureDto,
    CaptureReceipt,
    SemanticInstanceMapping,
    decode_instance,
)
from app.native_query.contracts import Sha256

ELIGIBLE_CLASSES = {
    "AcDbText": "text_annotation",
    "AcDbMText": "mtext_annotation",
    "AcDbAttribute": "attribute_instance_value",
    "AcDbAttributeDefinition": "attribute_definition_default",
}
MAX_VALUE_BYTES = 4096
MAX_RAW_VALUE_BYTES = 16 * 1024**2
MAX_OBSERVATIONS_PER_LAYER = 32
_DIMENSION = re.compile(
    r"d\s*=\s*(?:(?P<count>[1-9][0-9]?)\s*[xх]\s*)?"
    r"(?P<size>[0-9]{1,5}(?:[.,][0-9]{1,3})?)\s*"
    r"(?P<material>ст\.|чуг\.|ж\.б\.|ац\.|кер\.|вчшг\.)?\s*"
    r"(?:(?P<width>[0-9]{1,5})\s*[xх]\s*(?P<height>[0-9]{1,5}))?\s*"
    r"(?P<suffix>(?:б\.к\.)?(?:бд)?)",
    re.IGNORECASE,
)
_NOMINAL = re.compile(r"dn\s*(?P<size>[0-9]{1,5})", re.IGNORECASE)
_COVERS = {
    "газон": "lawn_label",
    "цветник": "flowerbed_label",
    "асфальт": "asphalt_label",
    "бетон": "concrete_label",
    "грунт": "soil_label",
    "щебень": "gravel_label",
}
_MATERIALS = {
    "ст.": "steel",
    "чуг.": "cast_iron",
    "ж.б.": "reinforced_concrete",
    "ац.": "asbestos_cement",
    "кер.": "ceramic",
    "вчшг.": "ductile_iron",
}


class SemanticRow(SemanticInstanceMapping):
    instance_index: int = Field(ge=0)
    origin: Literal[
        "text_annotation",
        "mtext_annotation",
        "attribute_instance_value",
        "attribute_definition_default",
    ]
    value: str
    tag: str
    anchor_local: tuple[FiniteFloat, FiniteFloat, FiniteFloat] | None
    anchor_wcs: tuple[FiniteFloat, FiniteFloat, FiniteFloat] | None
    invisible: bool
    status: Literal[
        "captured", "value_limit", "unreadable", "invalid_anchor", "budget_limit"
    ]

    @field_validator("value", "tag")
    @classmethod
    def bounded_value(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
            raise ValueError("Native label exceeds value limit")
        return value

    @model_validator(mode="after")
    def origin_and_status(self) -> SemanticRow:
        if ELIGIBLE_CLASSES.get(self.class_) != self.origin:
            raise ValueError("Native semantic origin disagrees with its class")
        if self.status != "captured" and (
            self.value
            or self.tag
            or self.anchor_local is not None
            or self.anchor_wcs is not None
        ):
            raise ValueError("Incomplete native evidence must not carry values")
        if self.status == "captured" and (
            self.anchor_local is None or self.anchor_wcs is None or self.unavailable
        ):
            raise ValueError("Captured native evidence requires readable anchors")
        return self


class NativeSemantics(CaptureDto):
    schema_: Literal["green-atlas.native-semantics/1"] = Field(alias="schema")
    instances_sha256: Sha256
    display_sha256: Sha256
    instance_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    emitted_count: int = Field(ge=0, le=MAX_SEMANTIC_ROWS)
    omitted_count: int = Field(ge=0)
    complete: bool
    rows: tuple[SemanticRow, ...] = Field(max_length=MAX_SEMANTIC_ROWS)

    @model_validator(mode="after")
    def consistent_counts(self) -> NativeSemantics:
        if (
            len(self.rows) != self.emitted_count
            or self.eligible_count != self.emitted_count + self.omitted_count
            or self.eligible_count > self.instance_count
            or len({row.instance_index for row in self.rows}) != len(self.rows)
            or any(row.instance_index >= self.instance_count for row in self.rows)
            or self.complete
            != (
                not self.omitted_count
                and all(row.status == "captured" for row in self.rows)
            )
            or sum(len(row.value.encode()) + len(row.tag.encode()) for row in self.rows)
            > MAX_RAW_VALUE_BYTES
        ):
            raise ValueError("Native semantic completeness disagrees with its rows")
        return self


def label_observation(value: str, origin: str) -> LabelObservation | None:
    # Допускается только полное совпадение: выделение подстрок передавало бы имена,
    # поля штампов и произвольные инструкции вокруг технической подписи.
    if origin == "attribute_definition_default" or len(value) > 256:
        return None
    text = value.strip().casefold().replace("ё", "е")
    cover = _COVERS.get(text)
    if cover:
        return LabelObservation.model_validate({"kind": cover, "count": 1})
    nominal = _NOMINAL.fullmatch(text)
    if nominal and int(nominal["size"]) > 0:
        return LabelObservation(
            kind="nominal_designation_note", count=1, value=int(nominal["size"])
        )
    match = _DIMENSION.fullmatch(text)
    if not match or float(match["size"].replace(",", ".")) <= 0:
        return None
    section = (int(match["width"]), int(match["height"])) if match["width"] else None
    if section and min(section) <= 0:
        return None
    return LabelObservation.model_validate(
        {
            "kind": "dimension_notation",
            "count": 1,
            "value": float(match["size"].replace(",", ".")),
            "count_notation": int(match["count"] or "1"),
            "material_notation": _MATERIALS.get(match["material"]),
            "section_notation": section,
        }
    )


def _anchor_matches(row: SemanticRow) -> bool:
    if row.anchor_local is None or row.anchor_wcs is None:
        return row.status != "captured"
    source = (*row.anchor_local, 1)
    transformed = tuple(
        sum(value * source[index] for index, value in enumerate(axis))
        for axis in row.transform
    )
    return abs(transformed[3] - 1) <= 1e-9 and all(
        abs(actual - expected) <= max(1e-7, abs(expected) * 1e-12)
        for actual, expected in zip(transformed[:3], row.anchor_wcs, strict=True)
    )


def read_semantics(
    root: Path,
    receipt: CaptureReceipt,
    check: Callable[[], None],
) -> tuple[NativeSemantics, dict[str, LayerSemanticEvidence]]:
    if receipt.schema_ != "green-atlas.native-session/3":
        raise ValueError("Native capture has no versioned semantic evidence")
    with read_regular(root / "semantics.json") as stream:
        content = stream.read(MAX_SEMANTICS_BYTES + 1)
    if (
        len(content) != receipt.semantics_bytes
        or sha256(content).hexdigest() != receipt.semantics_sha256
    ):
        raise ValueError("Native semantic file identity changed")
    evidence = NativeSemantics.model_validate_json(content)
    if (
        evidence.instances_sha256 != receipt.instances_sha256
        or evidence.display_sha256 != receipt.display_sha256
        or evidence.instance_count != receipt.instance_count
        or evidence.emitted_count != receipt.semantics_count
    ):
        raise ValueError("Native semantics belong to another capture")
    rows = {row.instance_index: row for row in evidence.rows}
    eligible: Counter[str] = Counter()
    captured: Counter[str] = Counter()
    emitted: Counter[str] = Counter()
    defaults: Counter[str] = Counter()
    observations: dict[str, Counter[str]] = defaultdict(Counter)
    classes = {entry.path for entry in receipt.files}
    source_routes: set[str] = set()
    archive_routes: set[str] = set()
    count = 0
    with read_regular(root / "instances.json") as stream:
        digest = sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            check()
            digest.update(chunk)
        if digest.hexdigest() != receipt.instances_sha256:
            raise ValueError("Native semantic instance index identity changed")
        stream.seek(0)
        for index, value in enumerate(ijson.items(stream, "item", use_float=True)):
            check()
            count += 1
            instance = decode_instance(value, receipt.schema_)
            if (
                not isinstance(instance, SemanticInstanceMapping)
                or instance.archive_file not in classes
                or instance.source_route in source_routes
                or instance.archive_route in archive_routes
            ):
                raise ValueError("Native semantic instance lost its captured database")
            source_routes.add(instance.source_route)
            archive_routes.add(instance.archive_route)
            is_eligible = instance.class_ in ELIGIBLE_CLASSES
            if is_eligible:
                eligible[instance.effective_layer] += 1
            row = rows.pop(index, None)
            if row is None:
                continue
            if (
                not is_eligible
                or any(
                    getattr(row, field) != getattr(instance, field)
                    for field in SemanticInstanceMapping.model_fields
                )
                or not _anchor_matches(row)
            ):
                raise ValueError(
                    "Native label is not bound to its exact source instance"
                )
            layer = instance.effective_layer
            emitted[layer] += 1
            if row.status != "captured":
                continue
            captured[layer] += 1
            if row.origin == "attribute_definition_default":
                defaults[layer] += 1
            hint = label_observation(row.value, row.origin)
            if hint:
                key = json.dumps(hint.model_dump(exclude={"count"}), sort_keys=True)
                observations[layer][key] += 1
    if (
        rows
        or count != receipt.instance_count
        or sum(eligible.values()) != evidence.eligible_count
    ):
        raise ValueError("Native semantic catalogue is incomplete or contradictory")
    summaries = {}
    for layer, total in eligible.items():
        counts = observations[layer]
        ordered = sorted(counts, key=lambda key: (-counts[key], key))[
            :MAX_OBSERVATIONS_PER_LAYER
        ]
        summaries[layer] = LayerSemanticEvidence(
            source_sha256=receipt.display_sha256,
            evidence_sha256=receipt.semantics_sha256 or "",
            complete=captured[layer] == total
            and len(counts) <= MAX_OBSERVATIONS_PER_LAYER,
            eligible_count=total,
            captured_count=captured[layer],
            omitted_count=total - emitted[layer],
            default_count=defaults[layer],
            observations=tuple(
                LabelObservation.model_validate(
                    {**json.loads(key), "count": counts[key]}
                )
                for key in ordered
            ),
        )
    return evidence, summaries


def attach_semantics(
    root: Path,
    source_sha256: str,
    layers: list[Layer],
) -> None:
    from app.native_query.capture.capture_store import read_capture_receipt

    receipt = read_capture_receipt(root)
    if receipt.display_sha256 != source_sha256:
        raise ValueError("Native semantics belong to another display snapshot")
    if receipt.schema_ != "green-atlas.native-session/3":
        return
    _, summaries = read_semantics(root, receipt, lambda: None)
    by_name = {layer.source_name: layer for layer in layers}
    if len(by_name) != len(layers) or any(name not in by_name for name in summaries):
        raise ValueError("Native labels do not match the imported layer catalogue")
    for name, summary in summaries.items():
        by_name[name].semantic_evidence = summary
