"""Select transformed leaves before loading a large block's dependency graph."""

import logging
from dataclasses import dataclass, field

from ezdxf import transform
from ezdxf.document import Drawing
from ezdxf.entities import Attrib, DXFEntity, DXFGraphic, Insert, MultiLeader
from ezdxf.explode import attrib_to_text
from ezdxf.math import Matrix44

from app.cad_import.aoi_policy import AoiPolicy
from app.cad_import.aoi_selection import MAX_BOUND_DEPTH, AoiSelection
from app.cad_import.contracts import CadConversionError

PROGRESS_INTERVAL = 10_000
IDENTITY_TRANSFORM = tuple(Matrix44())
MAX_PROXY_DIAGNOSTIC_EXAMPLES = 10


@dataclass(frozen=True)
class LeafOrigin:
    handle: str
    chain: list[str]
    matrix: list[float]


@dataclass
class AoiLeaves:
    document: Drawing
    selection: AoiSelection
    policy: AoiPolicy
    origins: dict[str, LeafOrigin] = field(default_factory=dict)
    visited: int = 0
    known_primitives: int = 0
    diagnostics: list[str] = field(default_factory=list)
    proxy_invalidated_count: int = 0
    proxy_invalidated_examples: list[str] = field(default_factory=list)

    def collect(self) -> None:
        # The modelspace list is shallow; selected virtual leaves are bound only
        # after filtering so discarded block contents never enter a loader graph.
        for entity in list(self.document.modelspace()):
            self.visit(entity, Matrix44(), [], frozenset(), "0")
        if self.proxy_invalidated_count:
            self.diagnostics.append(
                f"Библиотека сбросила proxy_graphic у {self.proxy_invalidated_count} "
                "преобразованных экземпляров; исходные байты сохранены в полном CAD. "
                f"Первые {len(self.proxy_invalidated_examples)}: "
                + "; ".join(self.proxy_invalidated_examples)
            )

    def visit(
        self,
        entity: DXFEntity,
        matrix: Matrix44,
        chain: list[str],
        ancestry: frozenset[str],
        inherited_layer: str,
    ) -> None:
        self.visited += 1
        if self.visited % PROGRESS_INTERVAL == 0:
            logging.getLogger(__name__).info(
                "AOI visited=%d selected=%d excluded=%d",
                self.visited,
                len(self.selection.selected),
                self.selection.excluded,
            )
        if self.visited > self.policy.max_visited_entities:
            raise CadConversionError(
                "Превышен бюджет обхода CAD для рабочей территории"
            )
        layer = str(entity.dxf.get("layer", "0"))
        layer = inherited_layer if layer == "0" else layer
        if not isinstance(entity, Insert):
            self.leaf(entity, matrix, chain, layer)
            return
        block = entity.block()
        if (
            block is None
            or entity.dxf.name in ancestry
            or len(ancestry) >= MAX_BOUND_DEPTH
        ):
            self.diagnostics.append(
                f"INSERT #{entity.dxf.handle}: отсутствующий/циклический блок"
            )
            return
        if block.block is None or block.block.is_xref:
            self.diagnostics.append(
                f"XREF #{entity.dxf.handle} {entity.dxf.name}: внешнее содержимое не встроено"
            )
            self.leaf(entity, matrix, chain, layer)
            return
        instances = entity.multi_insert() if entity.mcount > 1 else [entity]
        for number, instance in enumerate(instances):
            if number >= self.policy.max_visited_entities:
                raise CadConversionError("Превышен бюджет экземпляров MINSERT")
            next_chain = [*chain, f"{entity.dxf.handle}:{number}"]
            combined = instance.matrix44() @ matrix
            for attribute in instance.attribs:
                self.leaf(attribute, matrix, next_chain, layer)
            for child in block:
                if child.dxftype() == "ATTDEF":
                    self.diagnostics.append(
                        f"ATTDEF #{child.dxf.handle}: шаблон блока, значения берутся из ATTRIB экземпляра"
                    )
                    continue
                self.visit(
                    child, combined, next_chain, ancestry | {entity.dxf.name}, layer
                )

    def leaf(
        self, entity: DXFEntity, matrix: Matrix44, chain: list[str], layer: str
    ) -> None:
        # A transformed polyline may yield several primitives. Decide once for
        # the complete source object, then retain every resulting primitive.
        unknown_before = self.selection.unknown
        if not self.selection.accepts(entity, matrix):
            return
        known_bounds = unknown_before == self.selection.unknown
        handle = str((entity.origin_of_copy or entity).dxf.handle)
        try:
            # MultiLeader.transform clears its proxy even for an identity matrix.
            # Preserve that cache only when no coordinate transformation occurs.
            copy_matrix = (
                None
                if isinstance(entity, MultiLeader)
                and tuple(matrix) == IDENTITY_TRANSFORM
                else matrix
            )
            log, copies = transform.copies([entity], copy_matrix)
        except Exception as error:
            self.diagnostics.append(f"{entity.dxftype()} #{handle}: {error}")
            return
        self.diagnostics.extend(f"#{handle}: {entry.message}" for entry in log)
        if chain and entity.dxftype() in {"TEXT", "MTEXT", "ATTRIB"}:
            self.diagnostics.append(
                f"Текст #{handle}: положение перенесено из INSERT, типографика требует проверки"
            )
        for copy in copies:
            if entity.proxy_graphic and copy.proxy_graphic is None:
                self.proxy_invalidated_count += 1
                if len(self.proxy_invalidated_examples) < MAX_PROXY_DIAGNOSTIC_EXAMPLES:
                    self.proxy_invalidated_examples.append(
                        f"{'/'.join(chain)} / {entity.dxftype()} #{handle}"
                    )
            if isinstance(copy, Attrib):
                copy = attrib_to_text(copy)
            if not isinstance(copy, DXFGraphic):
                self.diagnostics.append(
                    f"{entity.dxftype()} #{handle}: не графическая сущность"
                )
                continue
            copy.dxf.layer = layer
            if len(self.selection.selected) >= self.policy.max_selected_entities:
                raise CadConversionError("Превышен бюджет объектов рабочей территории")
            self.selection.selected.append(copy)
            if known_bounds:
                self.known_primitives += 1
            self.document.modelspace().add_entity(copy)
            self.origins[copy.dxf.handle] = LeafOrigin(
                handle,
                list(chain),
                list(matrix),
            )
