"""Адресная диагностика непрочитанных компонентов DXF-блоков без изменения
CAD-документа.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.legacy.entities.block_capacity import inspect_block_expansion
from app.dxf_import.legacy.entities.component_provenance import (
    CONTEXT_ONLY_ENTITY_TYPES,
    MAX_RENDERED_MESH_FACES,
    _is_mesh_context,
    _mesh_face_count,
)
from app.dxf_import.legacy.entities.polygons import _point

SUPPORTED_TYPES = {
    "LINE",
    "LWPOLYLINE",
    "POLYLINE",
    "CIRCLE",
    "ARC",
    "ELLIPSE",
    "SPLINE",
    "HATCH",
    "MPOLYGON",
    "3DFACE",
    "SOLID",
    "TRACE",
    "POINT",
    "TEXT",
    "MTEXT",
    "ATTDEF",
    "INSERT",
    "DIMENSION",
    "WIPEOUT",
    # Эти сущности часто встречаются в топосъёмке. По возможности показываем их
    # геометрию, сохраняя вне предметных ограничений посадки.
    "LEADER",
    "MLEADER",
    "MULTILEADER",
    "MLINE",
    "HELIX",
    "MESH",
    "RAY",
    "XLINE",
    "IMAGE",
    "PDFUNDERLAY",
    "PDFREFERENCE",
    "DWFUNDERLAY",
    "DGNUNDERLAY",
    "ACAD_PROXY_ENTITY",
}


GeometryProjector = Callable[[Any, float], dict[str, Any] | None]
InsertComponents = list[list[tuple[dict[str, Any], Any, str]]]
MAX_NESTED_BLOCK_DEPTH = 16
MAX_EXPANDED_MINSERT_INSTANCES = 20_000


def source_handle(entity: Any) -> str:
    original = getattr(entity, "origin_of_copy", None)
    source = original if original is not None else entity
    return str(source.dxf.get("handle", "") or "?")


@dataclass(frozen=True)
class BlockGeometryFailure:
    source_layer: str
    entity_type: str
    source_handle: str
    insert_path: tuple[str, ...]
    reason: str

    def warning(self) -> str:
        return (
            f"Блок {' / '.join(self.insert_path)}, "
            f"объект {self.entity_type} #{self.source_handle}, "
            f"слой «{self.source_layer}»: {self.reason}. "
            "Исходный объект сохранён; расчётное представление требует проверки."
        )


def record_failure(
    failures: list[BlockGeometryFailure] | None,
    entity: Any,
    inherited_layer: str,
    insert_path: tuple[str, ...],
    reason: str,
) -> None:
    if failures is None:
        return
    layer = str(entity.dxf.get("layer", "0") or "0")
    failures.append(
        BlockGeometryFailure(
            source_layer=inherited_layer if layer == "0" else layer,
            entity_type=entity.dxftype(),
            source_handle=source_handle(entity),
            insert_path=insert_path,
            reason=reason,
        )
    )


def _insert_instance_count(entity: Any) -> int:
    """Осторожно читать объявленное количество экземпляров INSERT/MINSERT."""
    try:
        return max(1, int(getattr(entity, "mcount", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _flatten_insert_components(
    reference: Any,
    factor: float,
    inherited_layer: str,
    project_geometry: GeometryProjector,
    depth: int = 0,
    failures: list[BlockGeometryFailure] | None = None,
    insert_path: tuple[str, ...] = (),
) -> list[tuple[dict[str, Any], Any, str]]:
    """Разворачивать виртуальные блоки с сохранением явно заданных слоёв компонентов.

    Insert.virtual_entities() применяет вложенные трансформации, но возвращает
    вложенный INSERT как INSERT. Обход до компонентов сохраняет исходные роли сетей и
    границ внутри общего символа.
    """
    if depth >= MAX_NESTED_BLOCK_DEPTH:
        record_failure(
            failures,
            reference,
            inherited_layer,
            insert_path,
            "Достигнута граница вложенности блоков",
        )
        return []
    components: list[tuple[dict[str, Any], Any, str]] = []
    try:
        count = _insert_instance_count(reference)
        if count > MAX_EXPANDED_MINSERT_INSTANCES:
            record_failure(
                failures,
                reference,
                inherited_layer,
                insert_path,
                "Превышен предел экземпляров массива",
            )
            return []
        references = reference.multi_insert() if count > 1 else [reference]
        for instance_index, instance in enumerate(references, start=1):
            path = (*insert_path, f"{source_handle(reference)}[{instance_index}]")

            def skipped(virtual: Any, reason: str) -> None:
                record_failure(failures, virtual, inherited_layer, path, reason)

            for virtual in instance.virtual_entities(skipped_entity_callback=skipped):
                declared_layer = str(virtual.dxf.get("layer", "0") or "0")
                effective_layer = (
                    inherited_layer if declared_layer == "0" else declared_layer
                )
                if virtual.dxftype() == "INSERT":
                    components.extend(
                        _flatten_insert_components(
                            virtual,
                            factor,
                            effective_layer,
                            project_geometry,
                            depth + 1,
                            failures,
                            path,
                        )
                    )
                    continue
                geometry = project_geometry(virtual, factor)
                if geometry is not None:
                    components.append((geometry, virtual, effective_layer))
                elif (
                    virtual.dxftype() in SUPPORTED_TYPES
                    and virtual.dxftype() not in CONTEXT_ONLY_ENTITY_TYPES
                ):
                    record_failure(
                        failures,
                        virtual,
                        inherited_layer,
                        path,
                        "Не удалось получить полный плоский контур",
                    )
    except Exception as error:
        record_failure(
            failures,
            reference,
            inherited_layer,
            insert_path,
            f"Ошибка раскрытия блока: {error}",
        )
        return []
    return components


def _block_definition_components(
    reference: Any, inherited_layer: str, depth: int = 0
) -> list[tuple[str, str]] | None:
    """Читать исходные компоненты определения блока с их действующими слоями.

    Некоторые сущности исчезают из virtual_entities при ошибке трансформации;
    исходное определение ещё позволяет установить их слой. None означает недоступное
    определение, а не намеренно пустой блок.
    """
    if depth >= MAX_NESTED_BLOCK_DEPTH:
        return None
    components: list[tuple[str, str]] = []
    try:
        for component in reference.block():
            declared_layer = str(component.dxf.get("layer", "0") or "0")
            effective_layer = (
                inherited_layer if declared_layer == "0" else declared_layer
            )
            if component.dxftype() == "INSERT":
                nested = _block_definition_components(
                    component, effective_layer, depth + 1
                )
                if nested is None:
                    return None
                components.extend(nested)
            else:
                components.append((component.dxftype(), effective_layer))
    except Exception:
        return None
    return components


def _insert_instance_components(
    entity: Any,
    factor: float,
    project_geometry: GeometryProjector,
    failures: list[BlockGeometryFailure] | None = None,
) -> list[list[tuple[dict[str, Any], Any, str]]]:
    """Разворачивать INSERT в компоненты экземпляра с действующими слоями.

    Слой 0 наследует слой родительского INSERT; ненулевой слой принадлежит компоненту
    и остаётся доступным для назначения и ограничений.
    """
    parent_layer = str(entity.dxf.layer)
    try:
        instance_count = _insert_instance_count(entity)
        references = entity.multi_insert() if instance_count > 1 else [entity]
        instances: list[list[tuple[dict[str, Any], Any, str]]] = []
        for instance_index, reference in enumerate(references, start=1):
            path = (
                (f"{source_handle(entity)}[{instance_index}]",)
                if instance_count > 1
                else ()
            )
            instances.append(
                _flatten_insert_components(
                    reference, factor, parent_layer, project_geometry, 0, failures, path
                )
            )
        return instances
    except Exception as error:
        record_failure(
            failures,
            entity,
            parent_layer,
            (source_handle(entity),),
            f"Ошибка раскрытия массива: {error}",
        )
        return []


def _insert_instance_geometries(
    entity: Any,
    factor: float,
    project_geometry: GeometryProjector,
    components_by_instance: InsertComponents | None = None,
) -> list[dict[str, Any]]:
    """Сохранять отдельную геометрию каждого видимого экземпляра INSERT/MINSERT.

    MINSERT записан одной сущностью, но каждая позиция влияет на карту и расчёт.
    Раздельные объекты ограничивают карту без потери точного backend-покрытия.
    """
    try:
        instances: list[dict[str, Any]] = []
        references = (
            list(entity.multi_insert())
            if _insert_instance_count(entity) > 1
            else [entity]
        )
        if components_by_instance is None:
            components_by_instance = _insert_instance_components(
                entity, factor, project_geometry
            )
        for reference, components in zip(
            references, components_by_instance, strict=False
        ):
            geometries = [geometry for geometry, _virtual, _layer in components]
            if geometries:
                instances.append(
                    {"type": "GeometryCollection", "geometries": geometries}
                )
            else:
                instances.append(
                    {
                        "type": "Point",
                        "coordinates": _point(reference.dxf.insert, factor),
                    }
                )
        return instances
    except Exception:
        return []


@dataclass
class LayerInventory:
    """Ordered source counts and explicit gaps for each effective layer."""

    counts: Counter[str] = field(default_factory=Counter)
    entity_types: dict[str, Counter[str]] = field(default_factory=dict)
    unsupported: Counter[str] = field(default_factory=Counter)
    unsupported_by_layer: dict[str, Counter[str]] = field(default_factory=dict)
    unreadable: Counter[str] = field(default_factory=Counter)

    @classmethod
    def from_entities(cls, entities: list[Any]) -> "LayerInventory":
        result = cls()
        for entity in entities:
            layer = str(entity.dxf.layer)
            entity_type = entity.dxftype()
            result.counts[layer] += 1
            result.entity_types.setdefault(layer, Counter())[entity_type] += 1
            if entity_type not in SUPPORTED_TYPES:
                result.unsupported[entity_type] += 1
                result.unsupported_by_layer.setdefault(layer, Counter())[
                    entity_type
                ] += 1
        return result


@dataclass
class BlockInspection:
    """One document's reused projection and addressed block-admission findings."""

    explicit_components: dict[int, InsertComponents] = field(default_factory=dict)
    compact_geometries: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    context_only_inserts: set[int] = field(default_factory=set)
    oversized_arrays: dict[int, int] = field(default_factory=dict)
    oversized_meshes: dict[int, int] = field(default_factory=dict)
    failures: list[BlockGeometryFailure] = field(default_factory=list)


def inspect_document_blocks(
    entities: list[Any],
    factor: float,
    inventory: LayerInventory,
    capacity: SourceGeometryCapacity,
    project_geometry: GeometryProjector,
) -> BlockInspection:
    result = BlockInspection()
    definitions: dict[tuple[str, str], list[tuple[str, str]] | None] = {}
    for index, entity in enumerate(entities):
        if _is_mesh_context(entity):
            face_count = _mesh_face_count(entity)
            if face_count > MAX_RENDERED_MESH_FACES:
                result.oversized_meshes[index] = face_count
        if entity.dxftype() != "INSERT":
            continue
        instance_count = _insert_instance_count(entity)
        expansion = inspect_block_expansion(
            entity,
            max_components=capacity.max_features,
            max_array_instances=MAX_EXPANDED_MINSERT_INSTANCES,
            max_depth=MAX_NESTED_BLOCK_DEPTH,
        )
        if expansion.blocked:
            # Раскрытие multi_insert ради проверки слоёв обошло бы лимит импорта.
            # Для вложенного массива также нельзя показывать лишь первый элемент:
            # невидимая геометрия выглядела бы полностью доступной.
            result.oversized_arrays[index] = expansion.components
            continue
        parent_layer = str(entity.dxf.layer)
        definition_key = (str(entity.dxf.name), parent_layer)
        if definition_key not in definitions:
            definitions[definition_key] = _block_definition_components(
                entity, parent_layer
            )
        definition_components = definitions[definition_key]
        for entity_type, component_layer in definition_components or []:
            if entity_type in SUPPORTED_TYPES:
                continue
            inventory.unsupported[entity_type] += 1
            inventory.unsupported_by_layer.setdefault(component_layer, Counter())[
                entity_type
            ] += 1
            inventory.counts[component_layer] += 1
            inventory.entity_types.setdefault(component_layer, Counter())[
                entity_type
            ] += 1
        failures: list[BlockGeometryFailure] = []
        instances = _insert_instance_components(
            entity, factor, project_geometry, failures
        )
        result.failures.extend(failures)
        diagnosed_components: Counter[tuple[str, str]] = Counter()
        for failure in failures:
            if (
                failure.entity_type not in SUPPORTED_TYPES
                or failure.entity_type in CONTEXT_ONLY_ENTITY_TYPES
            ):
                continue
            component_layer = failure.source_layer
            inventory.unreadable[component_layer] += 1
            diagnosed_components[failure.entity_type, component_layer] += 1
            if component_layer != parent_layer:
                inventory.counts[component_layer] += 1
            inventory.entity_types.setdefault(component_layer, Counter())[
                failure.entity_type
            ] += 1
        components = [
            virtual for instance in instances for _geometry, virtual, _layer in instance
        ]
        if not components:
            if definition_components is None:
                # Неразрешённый или повреждённый блок может иметь неизвестный физический
                # габарит на действующем слое INSERT.
                inventory.unreadable[parent_layer] += 1
            else:
                for entity_type, component_layer in definition_components:
                    if (
                        entity_type in SUPPORTED_TYPES
                        and entity_type not in CONTEXT_ONLY_ENTITY_TYPES
                    ):
                        if diagnosed_components[entity_type, component_layer]:
                            diagnosed_components[entity_type, component_layer] -= 1
                            continue
                        inventory.unreadable[component_layer] += 1
                        # Сбой виртуальной трансформации может скрыть явно назначенный слой компонента.
                        # Сохраняем запись слоя, чтобы оператор мог принять адресное решение об исключении.
                        if component_layer != parent_layer:
                            inventory.counts[component_layer] += 1
                        inventory.entity_types.setdefault(component_layer, Counter())[
                            entity_type
                        ] += 1
        # Текстовый блок может находиться на слое BUILDING или ROAD; его якорь не создаёт
        # физическое препятствие. Проход проверки уже содержит виртуальные компоненты:
        # классифицируем их здесь без третьего раскрытия блока при построении объектов.
        if (
            not components
            and definition_components is not None
            and all(
                entity_type in CONTEXT_ONLY_ENTITY_TYPES
                for entity_type, _component_layer in definition_components
            )
        ) or (
            components
            and all(
                virtual.dxftype() in CONTEXT_ONLY_ENTITY_TYPES
                or _is_mesh_context(virtual)
                for virtual in components
            )
        ):
            result.context_only_inserts.add(index)
        if not any(
            component_layer != parent_layer
            for instance in instances
            for _geometry, _virtual, component_layer in instance
        ):
            # Проход проверки уже преобразовал этот INSERT. Повторно используем геометрию
            # в пределах одного документа без смешения разных экземпляров и трансформаций.
            result.compact_geometries[index] = _insert_instance_geometries(
                entity, factor, project_geometry, instances
            )
            if not result.compact_geometries[index] and instance_count == 1:
                # Сохраняем прежнюю совместимость одиночного INSERT: ошибка слоя блокирует
                # физическое использование, а исходный якорь остаётся доступным для просмотра.
                result.compact_geometries[index] = [
                    {"type": "Point", "coordinates": _point(entity.dxf.insert, factor)}
                ]
            continue
        result.explicit_components[index] = instances
        for instance in instances:
            for _geometry, virtual, component_layer in instance:
                if component_layer == parent_layer:
                    continue
                inventory.counts[component_layer] += 1
                inventory.entity_types.setdefault(component_layer, Counter())[
                    virtual.dxftype()
                ] += 1
    return result
