"""Copy CAD dependencies and retain explicit source handles in the derived file."""

from collections.abc import Iterable

from ezdxf import xref
from ezdxf.document import Drawing
from ezdxf.entities import DXFEntity, Insert

from app.cad_import.aoi_contracts import AoiEntityLink
from app.cad_import.contracts import CadConversionError


def copy_with_provenance(
    source: Drawing,
    target: Drawing,
    selected: list[DXFEntity],
    source_hash: str,
) -> list[AoiEntityLink]:
    appid = "GREEN_ATLAS_AOI_SOURCE"
    while appid in source.appids or appid in target.appids:
        appid += "_"
    source.appids.new(appid)
    expected: set[str] = set()

    def mark(entities: Iterable[DXFEntity], ancestry: frozenset[str]) -> None:
        for entity in entities:
            handle = entity.dxf.handle
            if not handle or handle in expected:
                continue
            expected.add(handle)
            entity.set_xdata(appid, [(1000, source_hash), (1000, handle)])
            if isinstance(entity, Insert):
                mark(entity.attribs, ancestry)
            block_name = (
                entity.dxf.name
                if isinstance(entity, Insert)
                else entity.dxf.get("geometry")
                if entity.dxftype() == "DIMENSION"
                else None
            )
            if block_name:
                if block_name in ancestry:
                    raise CadConversionError("Цикл зависимостей выбранного CAD-блока")
                block = source.blocks.get(block_name)
                if block is not None:
                    mark(block, ancestry | {block_name})

    mark(selected, frozenset())
    # The loader chooses the referenced styles. Mark their origin without
    # requiring unused table definitions to be copied into the derivative.
    for style in source.dimstyles:
        style.set_xdata(appid, [(1000, source_hash), (1000, style.dxf.handle)])
    handles = {entity.dxf.handle for entity in selected}
    loader = xref.Loader(source, target, conflict_policy=xref.ConflictPolicy.NUM_PREFIX)
    loader.load_modelspace(filter_fn=lambda entity: entity.dxf.handle in handles)
    loader.execute()
    links = []
    for entity in target.entitydb.values():
        if not entity.is_alive or not entity.has_xdata(appid):
            continue
        tags = entity.get_xdata(appid)
        links.append(
            AoiEntityLink(
                source_sha256=str(tags[0].value),
                source_handle=str(tags[1].value),
                output_handle=entity.dxf.handle,
                entity_type=entity.dxftype(),
                provenance_appid=appid,
            )
        )
    copied = {link.source_handle for link in links}
    if not expected.issubset(copied):
        raise CadConversionError(
            f"Не сохранены выбранные CAD-сущности/зависимости: {sorted(expected - copied)}"
        )
    return links
