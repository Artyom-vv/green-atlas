"""Участие экземпляров CAD в локальном расчёте: физическая роль, топология и
контекст сохраняют адрес объекта и основание подготовки одного захвата.
"""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Self

from pydantic import Field, model_validator

from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.review.contracts import PhysicalObjectKind, SourceObjectDecision
from app.native_query.contracts import NativeDto, Sha256
from app.native_query.live.live_inventory import InventoryObject, QueryObject


class SourceUse(StrEnum):
    PHYSICAL = "physical"
    TOPOLOGY = "topology"
    CONTEXT = "context"
    UNRESOLVED = "unresolved"


class SourceUseProof(StrEnum):
    CONFIRMED_LAYER = "confirmed_layer"
    OBJECT_DECISION = "object_decision"
    AREA_GROUP = "area_group"
    PREPARED_TOPOLOGY = "prepared_topology"
    PREPARED_BOUNDARY = "prepared_boundary"
    CAPTURE_CONTEXT = "capture_context"
    UNRESOLVED = "unresolved"


_SOURCE_USE_PROOFS: Mapping[SourceUse, frozenset[SourceUseProof]] = MappingProxyType(
    {
        SourceUse.PHYSICAL: frozenset(
            {
                SourceUseProof.CONFIRMED_LAYER,
                SourceUseProof.OBJECT_DECISION,
                SourceUseProof.AREA_GROUP,
            }
        ),
        SourceUse.TOPOLOGY: frozenset({SourceUseProof.PREPARED_TOPOLOGY}),
        SourceUse.CONTEXT: frozenset(
            {
                SourceUseProof.CONFIRMED_LAYER,
                SourceUseProof.OBJECT_DECISION,
                SourceUseProof.CAPTURE_CONTEXT,
                SourceUseProof.PREPARED_BOUNDARY,
            }
        ),
        SourceUse.UNRESOLVED: frozenset({SourceUseProof.UNRESOLVED}),
    }
)
_PREPARED_PROOF_USES: Mapping[SourceUseProof, SourceUse] = MappingProxyType(
    {
        SourceUseProof.PREPARED_TOPOLOGY: SourceUse.TOPOLOGY,
        SourceUseProof.PREPARED_BOUNDARY: SourceUse.CONTEXT,
    }
)


class SourceParticipation(NativeDto):
    use: SourceUse
    kind: LayerKind | None = None
    proof: SourceUseProof
    source_sha256: Sha256
    prepared_key: Sha256 | None = None
    source_layer: str = Field(min_length=1)

    @model_validator(mode="after")
    def coherent_use(self) -> Self:
        if (self.use == SourceUse.PHYSICAL) != (
            self.kind is not None and self.kind != LayerKind.IGNORE
        ):
            raise ValueError("Физическая роль не соответствует участию объекта")
        if self.use != SourceUse.PHYSICAL and self.kind is not None:
            raise ValueError("Контекст и топология не имеют физической роли")
        if self.proof not in _SOURCE_USE_PROOFS[self.use]:
            raise ValueError("Основание не соответствует участию объекта")
        if self.proof in _PREPARED_PROOF_USES and (
            self.prepared_key is None or self.use != _PREPARED_PROOF_USES[self.proof]
        ):
            raise ValueError("Участие границы требует основания CAD-подготовки")
        return self

    def current(self, source_sha256: str, prepared_key: str) -> bool:
        return self.source_sha256 == source_sha256 and self.prepared_key == prepared_key


def classify_source_use(
    layer: Layer | None,
    *,
    source_sha256: str,
    prepared_key: str | None,
    source_layer: str | None = None,
    decision: SourceObjectDecision | None = None,
    prepared_topology: bool = False,
    prepared_boundary: bool = False,
    capture_context: bool = False,
    area_group_kind: PhysicalObjectKind | None = None,
) -> SourceParticipation:
    """Классифицировать роль, не подменяя проверку геометрии назначением оператора.

    Флаги prepared_* передаёт владелец подготовки после проверки рецепта и
    native-проекции; имя слоя само по себе не является таким свидетельством.
    """
    kind: LayerKind | None = None
    if decision is not None and decision.interpretation == "reference":
        use, proof = SourceUse.CONTEXT, SourceUseProof.OBJECT_DECISION
    elif decision is not None and decision.kind is not None:
        use, proof = SourceUse.PHYSICAL, SourceUseProof.OBJECT_DECISION
        kind = LayerKind(decision.kind)
    elif area_group_kind is not None:
        use, proof = SourceUse.PHYSICAL, SourceUseProof.AREA_GROUP
        kind = LayerKind(area_group_kind)
    elif capture_context:
        use, proof = SourceUse.CONTEXT, SourceUseProof.CAPTURE_CONTEXT
    elif (
        layer is not None
        and layer.mapping_confirmed
        and layer.mapped_kind == LayerKind.SITE_BORDER
    ):
        # Граница территории может помогать сборке покрытия, сохраняя проверку
        # принадлежности посадки самой территории.
        use, proof = SourceUse.PHYSICAL, SourceUseProof.CONFIRMED_LAYER
        kind = LayerKind.SITE_BORDER
    elif prepared_topology:
        use, proof = SourceUse.TOPOLOGY, SourceUseProof.PREPARED_TOPOLOGY
    elif prepared_boundary:
        use, proof = SourceUse.CONTEXT, SourceUseProof.PREPARED_BOUNDARY
    elif (
        layer is not None and layer.mapping_confirmed and layer.mapped_kind is not None
    ):
        use, proof = SourceUse.PHYSICAL, SourceUseProof.CONFIRMED_LAYER
        if layer.mapped_kind == LayerKind.IGNORE:
            use = SourceUse.CONTEXT
        else:
            kind = layer.mapped_kind
    else:
        use, proof = SourceUse.UNRESOLVED, SourceUseProof.UNRESOLVED
    return SourceParticipation(
        use=use,
        kind=kind,
        proof=proof,
        source_sha256=source_sha256,
        prepared_key=prepared_key,
        source_layer=source_layer or (layer.source_name if layer is not None else ""),
    )


def source_object_key(item: QueryObject) -> tuple[tuple[str, ...], int]:
    return item.routes, item.face_id


def individual_object_targets(
    inventory: Mapping[str, InventoryObject],
    decisions: Mapping[str, SourceObjectDecision],
    reviewed_closures: frozenset[str],
) -> tuple[QueryObject, ...]:
    """Выделить изменённые экземпляры из автоматических групп, не меняя соседние."""
    result = []
    for route, decision in decisions.items():
        item = inventory.get(route)
        if item is None or item.context or item.error:
            raise ValueError("Решение об объекте не соответствует захвату AutoCAD")
        result.append(
            QueryObject(
                routes=(route,),
                layer=item.layer,
                bounds=None if route in reviewed_closures else item.bounds,
                curve=item.curve,
                error=item.error,
                reviewed_closure=route in reviewed_closures,
                reviewed_linear=decision.interpretation == "linear",
            )
        )
    return tuple(result)


def materialize_source_roles(
    objects: Iterable[QueryObject],
    layers: dict[str, Layer],
    participation: Mapping[tuple[tuple[str, ...], int], SourceParticipation],
) -> tuple[tuple[QueryObject, ...], tuple[QueryObject, ...]]:
    """Создать адресные расчётные роли, сохранив полный перечень для покрытия.

    Синтетический ключ нужен только внутреннему индексу. Имя исходного слоя и
    маршруты остаются прежними; назначение одного объекта не меняет весь слой 0.
    """
    sources, calculation = [], []
    for item in objects:
        use = participation[source_object_key(item)]
        if use.use == SourceUse.PHYSICAL and use.proof in {
            SourceUseProof.OBJECT_DECISION,
            SourceUseProof.AREA_GROUP,
        }:
            identity = sha256(repr(source_object_key(item)).encode()).hexdigest()
            key = "__green_atlas_object_role__/" + identity
            assert use.kind is not None
            layers[key] = Layer(
                id=identity,
                source_name=use.source_layer,
                suggested_kind=use.kind,
                mapped_kind=use.kind,
                mapping_confirmed=True,
                mapping_review_required=False,
                color=layers[use.source_layer].color
                if use.source_layer in layers
                else "#315bdf",
                object_count=len(item.routes),
            )
            item = item.model_copy(update={"layer": key})
        sources.append(item)
        if use.use in {SourceUse.PHYSICAL, SourceUse.UNRESOLVED}:
            calculation.append(item)
    return tuple(sources), tuple(calculation)
