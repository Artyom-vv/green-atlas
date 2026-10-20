"""Локальные обратимые псевдонимы и разрешённые признаки классификатора; исходные
CAD-строки остаются на устройстве.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from app.dxf_import.layers.contracts import Layer
from app.dxf_import.layers.recognition import (
    RECOGNITION_CATALOG_POLICY,
    LayerRoleProposal,
    category_from_layer,
    category_from_name,
)
from app.dxf_import.layers.suggestions import layer_subject
from app.layer_recognition.annotation_content import ANNOTATION_ENTITY_TYPES

FEATURE_POLICY = "anonymous-layer-features/4"


class NameHint(StrEnum):
    """Слова имени без исходной строки; наблюдение не назначает расчётную роль."""

    REVIEW_MARK = "review_mark"
    HOLDING = "holding"
    STREET_BOUNDARY = "street_boundary"
    SITE_BOUNDARY = "site_boundary"
    CESSPIT = "cesspit"
    RED_LINES = "regulatory_red_lines"
    CASING = "protective_casing"


# Передаём только фиксированные значения словаря. Сокращения и собственные
# названия проекта не раскрываются и не расшифровываются по соседним слоям.
_NAME_HINT_RULES = (
    (r"(?:^|[\W_])вопрос[а-я]*(?:$|[\W_])", NameHint.REVIEW_MARK),
    (r"(?:^|[\W_])удерживающ[а-я]*(?:$|[\W_])", NameHint.HOLDING),
    (r"границ[а-я]*[\W_]+улиц[а-я]*(?:$|[\W_])", NameHint.STREET_BOUNDARY),
    (r"границ[а-я]*[\W_]+площадк[а-я]*(?:$|[\W_])", NameHint.SITE_BOUNDARY),
    (r"выгребн[а-я]*[\W_]+ям[а-я]*(?:$|[\W_])", NameHint.CESSPIT),
    (r"красн[а-я]*[\W_]+лини[а-я]*(?:$|[\W_])", NameHint.RED_LINES),
    (r"(?:^|[\W_])футляр[а-я]*(?:$|[\W_])", NameHint.CASING),
)


def name_hint(source_name: str) -> str:
    """Сохранять разрешённый словесный признак, когда категория ещё неизвестна."""
    category = category_from_name(source_name)
    if category:
        return category.value
    subject = layer_subject(source_name)
    return next(
        (
            hint.value
            for pattern, hint in _NAME_HINT_RULES
            if re.search(pattern, subject)
        ),
        "unknown",
    )


def layer_name_hint(layer: Layer) -> str:
    """Признак имени использует ту же проверку геометрии, что локальное предложение."""
    category = category_from_layer(layer)
    return category.value if category is not None else name_hint(layer.source_name)


_KNOWN_TYPES = ANNOTATION_ENTITY_TYPES | {
    "ARC",
    "CIRCLE",
    "ELLIPSE",
    "HATCH",
    "INSERT",
    "LINE",
    "LWPOLYLINE",
    "MLINE",
    "POINT",
    "POLYLINE",
    "REGION",
    "SOLID",
    "SPLINE",
    "3DFACE",
}
_KNOWN_LINETYPES = frozenset(
    {
        "CONTINUOUS",
        "BYLAYER",
        "BYBLOCK",
        "DASHED",
        "DASHED2",
        "DASHEDX2",
        "HIDDEN",
        "HIDDEN2",
        "HIDDENX2",
        "CENTER",
        "CENTER2",
        "CENTERX2",
        "DASHDOT",
        "DASHDOT2",
        "DASHDOTX2",
        "DOT",
        "DOT2",
        "DOTX2",
        "PHANTOM",
    }
)


def _types(values: Mapping[str, int]) -> dict[str, int]:
    result: Counter[str] = Counter()
    for kind, count in values.items():
        if count > 0:
            result[kind if kind in _KNOWN_TYPES else "UNKNOWN"] += count
    return dict(sorted(result.items()))


def geometry_features(layer: Layer) -> dict[str, object]:
    """Сохранить состав геометрии и проверенный контекст без исходных CAD-строк."""
    if any(
        count < 0 or count > layer.entity_types.get(kind, 0)
        for kind, count in layer.context_entity_types.items()
    ):
        raise ValueError("Счётчик контекста не соответствует составу слоя")
    return {
        "projected_entity_types": _types(layer.projected_geometry_types),
        "context_entity_types": _types(layer.context_entity_types),
        "unreadable_count": layer.unreadable_geometry_count,
        "extent_m": (
            [
                round(abs(layer.bounds[2] - layer.bounds[0]), 2),
                round(abs(layer.bounds[3] - layer.bounds[1]), 2),
            ]
            if layer.bounds
            else None
        ),
    }


def recognition_basis(layers: Sequence[Layer]) -> dict[str, object]:
    """Полная локальная идентичность обоих проходов без изменяемого выбора оператора."""
    return {
        "feature_policy": FEATURE_POLICY,
        "subject_policy": RECOGNITION_CATALOG_POLICY,
        "layers": [
            layer.model_dump(
                exclude={
                    "mapped_kind",
                    "category",
                    "mapping_confirmed",
                    "mapping_review_required",
                    "required",
                    "visible",
                    "utility_context",
                    "utility_axis_bindings",
                }
            )
            for layer in layers
        ],
    }


class ExternalLayerContext:
    def __init__(self, context: Sequence[Layer]) -> None:
        self.context = tuple(context)
        self.aliases = {
            layer.id: f"layer-{index + 1}" for index, layer in enumerate(context)
        }
        if len(self.aliases) != len(context):
            raise ValueError("Повторяются идентификаторы слоёв")
        self.originals = {value: key for key, value in self.aliases.items()}
        self._layers = {layer.id: layer for layer in context}
        self._sources: dict[str, str] = {}
        for layer in context:
            prefix = layer.source_name.rpartition("|")[0]
            self._sources.setdefault(prefix, f"source-{len(self._sources) + 1}")

    def alias_for(self, identifier: str) -> str:
        try:
            return self.aliases[identifier]
        except KeyError:
            raise ValueError("Слой отсутствует в контексте распознавания") from None

    def first_pass_row(self, layer: Layer) -> dict[str, object]:
        if self._layers.get(layer.id) != layer:
            raise ValueError("Слой изменился относительно контекста распознавания")
        return {
            "layer_id": self.alias_for(layer.id),
            "name": layer_name_hint(layer),
            "object_count": layer.object_count,
            "entity_types": _types(layer.entity_types),
            "importer_suggestion": {
                "kind": layer.suggested_kind,
                "confidence": layer.suggestion_confidence,
            },
            "color": layer.color
            if re.fullmatch(r"#[0-9a-fA-F]{6}", layer.color)
            else None,
            "linetype": layer.linetype.upper()
            if layer.linetype.upper() in _KNOWN_LINETYPES
            else "UNKNOWN",
            "lineweight_mm": layer.lineweight_mm,
            "geometry_complete": layer.geometry_complete,
            "unsupported_geometry_types": _types(layer.unsupported_geometry_types),
            "geometry_support": geometry_features(layer),
            "boundary_candidate": (
                layer.boundary_candidate.model_dump(
                    mode="json", exclude={"basis", "issue"}
                )
                if layer.boundary_candidate
                else None
            ),
            "semantic_evidence": layer.semantic_evidence.external_features()
            if layer.semantic_evidence
            else None,
        }

    def catalog(self) -> list[dict[str, object]]:
        groups: dict[str, list[list[object]]] = {}
        for layer in self.context:
            prefix = layer.source_name.rpartition("|")[0]
            row = self.first_pass_row(layer)
            groups.setdefault(self._sources[prefix], []).append(
                [
                    row["layer_id"],
                    row["name"],
                    row["object_count"],
                    row["entity_types"],
                    layer.semantic_evidence.catalog_features()
                    if layer.semantic_evidence
                    else None,
                ]
            )
        return [
            {
                "xref_prefix": source,
                "columns": [
                    "layer_id",
                    "name_hint",
                    "object_count",
                    "entity_types",
                    "semantic_evidence",
                ],
                "rows": rows,
            }
            for source, rows in groups.items()
        ]

    def conflict_targets(
        self,
        layers: Sequence[Layer],
        proposals: Sequence[LayerRoleProposal],
    ) -> list[dict[str, object]]:
        by_id = {proposal.layer_id: proposal for proposal in proposals}
        if len(by_id) != len(proposals) or set(by_id) != {layer.id for layer in layers}:
            raise ValueError("Неверный набор предложений для проверки")
        return [
            {
                "layer": self.first_pass_row(layer),
                "geometry_support": geometry_features(layer),
                "first_pass": {
                    "layer_id": self.alias_for(layer.id),
                    "category": by_id[layer.id].category,
                    "calculation_role": by_id[layer.id].calculation_role,
                    "confidence": by_id[layer.id].confidence,
                    # Произвольные прежние объяснения могут повторять исходные строки и персональные данные.
                    "unresolved_count": len(by_id[layer.id].unresolved),
                },
            }
            for layer in layers
        ]

    def restore_reply(
        self,
        raw: str | bytes,
        response_field: str,
        requested: Sequence[Layer],
    ) -> str:
        payload: Any = json.loads(raw)
        replies = payload.get(response_field) if isinstance(payload, dict) else None
        expected = {self.alias_for(layer.id) for layer in requested}
        if not isinstance(replies, list) or len(replies) != len(expected):
            raise ValueError("Модель вернула другой набор слоёв")
        found: set[str] = set()
        for reply in replies:
            alias = reply.get("layer_id") if isinstance(reply, dict) else None
            if not isinstance(alias, str) or alias not in expected or alias in found:
                raise ValueError("Модель вернула другой набор слоёв")
            found.add(alias)
            reply["layer_id"] = self.originals[alias]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
