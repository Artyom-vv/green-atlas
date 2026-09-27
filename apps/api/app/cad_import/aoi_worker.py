"""Executed only in the resource-bounded AOI preparation subprocess."""

import gc
import json
import logging
import sys
from pathlib import Path
from time import perf_counter

import ezdxf
import psutil
from ezdxf.entities import LWPolyline
from ezdxf.math import Matrix44

from app.cad_import.aoi_annotations import repair_inactive_attachments
from app.cad_import.aoi_binary import save_binary_dxf
from app.cad_import.aoi_boundary import load_boundary
from app.cad_import.aoi_contracts import AoiManifest, AoiRequest, AoiSource
from app.cad_import.aoi_copy import copy_with_provenance
from app.cad_import.aoi_export_audit import (
    expected_export_omissions,
    verify_exported_handles,
)
from app.cad_import.aoi_leaves import AoiLeaves
from app.cad_import.aoi_policy import AoiPolicy
from app.cad_import.aoi_selection import AoiSelection
from app.cad_import.aoi_styles import normalize_dimension_colors
from app.cad_import.cache import file_sha256
from app.cad_import.contracts import CadConversionError
from app.dxf_import.preview_contracts import CadPreviewProvenance
from app.dxf_import.preview_marker import write_preview_marker
from app.dxf_import.units import meters_per_dxf_unit


def verify_source(source: AoiSource) -> None:
    for path, expected in (
        (source.original_path, source.original_sha256),
        (source.converted_path, source.converted_sha256),
    ):
        if file_sha256(path) != expected:
            raise CadConversionError(f"Источник CAD изменился: {path.name}")


def extract(
    request: AoiRequest, output: Path, policy: AoiPolicy | None = None
) -> AoiManifest:
    policy = policy or AoiPolicy()
    process = psutil.Process()
    stage_rss = {}
    started = perf_counter()

    def stage(name: str) -> None:
        stage_rss[name] = process.memory_info().rss
        logging.getLogger(__name__).info(
            "AOI stage=%s elapsed_seconds=%.3f rss_bytes=%d",
            name,
            perf_counter() - started,
            stage_rss[name],
        )

    stage("start")
    verify_source(request.source)
    verify_source(request.boundary_source)
    source = ezdxf.readfile(request.source.converted_path)
    stage("source_loaded")
    boundary_document = (
        source
        if request.source.converted_path.resolve()
        == request.boundary_source.converted_path.resolve()
        else ezdxf.readfile(request.boundary_source.converted_path)
    )
    boundary = load_boundary(
        boundary_document, request.boundary_handle, source.units, policy
    )
    loaded = perf_counter()
    factor = meters_per_dxf_unit(source.units)
    assert factor is not None
    radius = request.influence.radius_m
    selection = AoiSelection(
        boundary.polygon,
        ((radius or 0) + boundary.approximation.outward_margin_m) / factor,
    )
    leaves = AoiLeaves(source, selection, policy)
    leaves.collect()
    stage("leaves_selected")
    metrics = {
        "visited_entities": leaves.visited,
        "bounds_requests": selection.bounds_requests,
        "bounds_cache_hits": selection.cache_hits,
        "rejected_before_copy": selection.excluded,
        "rejected_before_shapely": selection.rectangle_rejects,
        "selected_primitives": len(selection.selected),
        "selected_known_primitives": leaves.known_primitives,
        "unknown_bounds": selection.unknown,
        "proxy_graphics_invalidated": leaves.proxy_invalidated_count,
    }
    logging.getLogger(__name__).info("AOI selection_metrics=%s", json.dumps(metrics))
    if not leaves.known_primitives:
        raise CadConversionError(
            "Для рабочей территории не получена геометрия с подтверждёнными "
            "границами. Подготовка остановлена до проверки координат, единиц "
            "и внешних ссылок; подписи и неизвестные границы не подтверждают полноту."
        )
    selected = perf_counter()
    target = ezdxf.new(source.dxfversion)
    target.units = source.units
    target.header["$INSBASE"] = source.header.get("$INSBASE", (0, 0, 0))
    links = copy_with_provenance(
        source, target, selection.selected, request.source.original_sha256
    )
    for link in links:
        origin = leaves.origins.get(link.source_handle)
        if origin is None:
            continue
        link.source_handle = origin.handle
        link.insert_chain = origin.chain
        link.world_transform = origin.matrix
        copied_entity = target.entitydb[link.output_handle]
        if link.provenance_appid:
            # Replace only this run's provenance, leaving pre-existing XDATA
            # intact even if an upstream application used the same prefix.
            copied_entity.discard_xdata(link.provenance_appid)
            copied_entity.set_xdata(
                link.provenance_appid,
                [
                    (1000, link.source_sha256),
                    (1000, origin.handle),
                    *[(1000, step) for step in origin.chain],
                ],
            )
    boundary_links = [
        link
        for link in links
        if link.source_sha256 == request.boundary_source.original_sha256
        and link.source_handle == request.boundary_handle
        and not link.insert_chain
    ]
    if not boundary_links:
        boundary_links = copy_with_provenance(
            boundary_document,
            target,
            [boundary.entity],
            request.boundary_source.original_sha256,
        )
        links.extend(boundary_links)
    if boundary.scale_to_source != 1:
        copied_boundary = target.entitydb[boundary_links[0].output_handle]
        assert isinstance(copied_boundary, LWPolyline)
        copied_boundary.transform(Matrix44.scale(boundary.scale_to_source))
    style_repairs = normalize_dimension_colors(
        target,
        {
            request.source.original_sha256: source,
            request.boundary_source.original_sha256: boundary_document,
        },
        links,
    )
    annotation_repairs = repair_inactive_attachments(target, links)
    export_omissions = expected_export_omissions(
        target,
        {
            request.source.original_sha256: source,
            request.boundary_source.original_sha256: boundary_document,
        },
        links,
    )
    stage("dependencies_copied")
    copied = perf_counter()
    warnings = selection.warnings
    if export_omissions:
        warnings.append(
            f"Во входном DXF отсутствуют ACIS-данные {len(export_omissions)} объектов; "
            "они не представлены в рабочем DXF. Источники перечислены в export_omissions."
        )
    if annotation_repairs:
        warnings.append(
            f"В {len(annotation_repairs)} выносках восстановлено неактивное поле "
            "вертикальной привязки из существующего контекста. Это совместимость "
            "предпросмотра; исходные значения и условия сохранены в annotation_repairs."
        )
    if style_repairs:
        warnings.append(
            f"В производном DXF восстановлено {len(style_repairs)} значений ACI "
            "из канонического raw-color DIMSTYLE. Исходные значения и ссылки "
            "сохранены в style_repairs; полный CAD не изменён."
        )
    if boundary.approximation.method == "bulge_arc_sagitta":
        warnings.append(
            f"Маска авторского контура дискретизирована с sagitta не более "
            f"{boundary.approximation.sagitta_tolerance_m:g} м; погрешность учтена наружу. "
            "Площадь маски приближённая; исходные дуги сохранены в DXF."
        )
    if not request.influence.verified:
        warnings.append(
            "Маска влияния не подтверждена нормативами; только предварительный просмотр"
        )
    xrefs = [
        block.name
        for block in source.blocks
        if block.block is not None and block.block.is_xref
    ]
    if xrefs:
        warnings.append(
            "В источнике есть внешние ссылки; автоматическое встраивание не выполнено: "
            + ", ".join(xrefs)
        )
    warnings.append(
        "Готовность расчёта требует отдельной проверки полноты источников и применимости правил"
    )
    warnings.append(
        "Рабочий DXF содержит геометрию экземпляров в мировых координатах; "
        "семантика INSERT, динамических блоков и XCLIP не сохранена. "
        "Наследование BYBLOCK-оформления и видимости требует отдельной проверки. "
        "Полный исходный CAD и результат конвертации сохраняются отдельно для выпуска."
    )
    write_preview_marker(
        target,
        CadPreviewProvenance(
            original_sha256=request.source.original_sha256,
            converted_sha256=request.source.converted_sha256,
            boundary_original_sha256=request.boundary_source.original_sha256,
            boundary_handle=request.boundary_handle,
            influence_radius_m=request.influence.radius_m,
            omitted_entity_count=len(export_omissions),
            boundary_bounds_m=(
                boundary.polygon.bounds[0] * factor,
                boundary.polygon.bounds[1] * factor,
                boundary.polygon.bounds[2] * factor,
                boundary.polygon.bounds[3] * factor,
            ),
        ),
    )
    binary_export = save_binary_dxf(target, output)
    verify_exported_handles(links, export_omissions, binary_export.exported_handles)
    leaves.diagnostics.append(
        f"Binary writer: {binary_export.decoded_hex_chunks} hex-фрагментов записаны исходными байтами"
    )
    stage("written")
    saved = perf_counter()
    # Publishing depends on unchanged full originals, not on copied CAD bytes alone.
    verify_source(request.source)
    verify_source(request.boundary_source)
    return AoiManifest(
        request=request,
        output_sha256=file_sha256(output),
        output_bytes=output.stat().st_size,
        output_format="binary_dxf",
        source_units=source.units,
        boundary_area_m2=boundary.area_m2,
        boundary_approximation=boundary.approximation,
        selected_modelspace_entities=len(selection.selected),
        excluded_by_bounds=selection.excluded,
        unknown_bounds=selection.unknown,
        source_links=links,
        warnings=warnings,
        derivation="world_space_leaves",
        diagnostics=leaves.diagnostics,
        style_repairs=style_repairs,
        annotation_repairs=annotation_repairs,
        export_omissions=export_omissions,
        stage_rss_bytes=stage_rss,
        selection_metrics=metrics,
        stage_seconds={
            "verify_and_load": loaded - started,
            "select": selected - loaded,
            "copy_dependencies": copied - selected,
            "write": saved - copied,
            "verify_originals": perf_counter() - saved,
        },
    )


def write_manifest(manifest: AoiManifest, path: Path) -> None:
    # Drawing graphs contain cycles. They are no longer needed after extract;
    # release them before allocating the serialized per-entity provenance.
    started = perf_counter()
    manifest.stage_rss_bytes["before_documents_release"] = (
        psutil.Process().memory_info().rss
    )
    gc.collect()
    manifest.stage_rss_bytes["documents_released"] = psutil.Process().memory_info().rss
    manifest.stage_seconds["release_documents"] = perf_counter() - started
    logging.getLogger(__name__).info(
        "AOI documents_released seconds=%.3f rss_before=%d rss_after=%d",
        manifest.stage_seconds["release_documents"],
        manifest.stage_rss_bytes["before_documents_release"],
        manifest.stage_rss_bytes["documents_released"],
    )
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    request = AoiRequest.model_validate_json(
        Path(sys.argv[1]).read_text(encoding="utf-8")
    )
    policy = AoiPolicy(**json.loads(Path(sys.argv[4]).read_text(encoding="utf-8")))
    manifest = extract(request, Path(sys.argv[2]), policy)
    write_manifest(manifest, Path(sys.argv[3]))
