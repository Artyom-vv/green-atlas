"""Import and layer mapping use cases, independent of HTTP and geometry engines."""

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from hashlib import sha256

from app.cad_bridge import CadSnapshot
from app.cad_bridge.provider import apply_cad_snapshot
from app.cad_intake.composition import ImportedDrawing, compose_dxf_imports
from app.dxf_import import limits
from app.dxf_import.admission import cad_preview_status
from app.dxf_import.assembly import assemble_imported_project
from app.dxf_import.contracts import (
    ImportEditability,
    ImportMode,
    ImportStatus,
    SourceFile,
)
from app.dxf_import.editor_source import open_source_editor
from app.dxf_import.layer_contracts import LayerKind, LayerMapping
from app.dxf_import.ports import DxfReaderPort, ImportProjectRepository
from app.dxf_import.review_contracts import SourceReview
from app.dxf_import.utility_mapping import (
    assign_utility_context,
    utility_contexts_by_layer,
)
from app.geometry.axis_bindings import validate_axis_bindings
from app.geometry.axis_contracts import UtilityAxisBinding
from app.geometry.contracts import GeometrySnapshot
from app.geometry.utility_contracts import UtilityContext
from app.history.ports import ProjectHistoryResetPort
from app.projects.contracts import Project, ProjectStatus
from app.releases.service import parse_release_bundle


class ImportApplication:
    def __init__(
        self,
        *,
        repository: ImportProjectRepository,
        dxf_reader: DxfReaderPort,
        history: ProjectHistoryResetPort,
        invalidate_spatial: Callable[[str], None],
        now: Callable[[], datetime],
    ) -> None:
        self.repository = repository
        self.dxf_reader = dxf_reader
        self.history = history
        self.invalidate_spatial = invalidate_spatial
        self.now = now

    def open_editor(self, project_id: str) -> Project:
        project = self.repository.get(project_id)
        opened = open_source_editor(project)
        if opened is project:
            return project
        saved = self.repository.save(opened)
        self.invalidate_spatial(project.id)
        return saved

    def import_dxf(
        self,
        project_id: str,
        filename: str,
        content: bytes | bytearray,
        cad_snapshot: bytes | bytearray | None = None,
    ) -> Project:
        project = self.repository.get(project_id)
        if project.plan is not None:
            # The original source and a manual plan form one auditable unit.
            # Replacing the source here would otherwise discard both the plan
            # and its undo history without a recoverable project revision.
            raise ValueError(
                "Нельзя заменить исходный DXF после открытия ручной схемы. Создайте новый проект."
            )
        limits.validate_dxf_filename(filename)
        if len(content) > limits.MAX_DXF_CONTENT_BYTES:
            raise ValueError(limits.dxf_size_error())
        imported = self.dxf_reader.read(filename, content)
        if cad_snapshot is not None:
            snapshot = CadSnapshot.model_validate_json(cad_snapshot)
            imported = apply_cad_snapshot(
                imported,
                snapshot,
                source_sha256=sha256(content).hexdigest(),
            )
        project = assemble_imported_project(
            project, filename, content, imported, self.now().isoformat()
        )
        saved = self.repository.save(project, source=content)
        self.invalidate_spatial(project.id)
        self.history.clear(project.id)
        return saved

    def import_release_bundle(
        self, project_id: str, filename: str, content: bytes | bytearray
    ) -> Project:
        """Restore an editable project revision from a validated release ZIP.

        Parsing happens before reading the target project or changing any
        durable state.  A malformed or stale bundle therefore cannot replace
        an existing source.  The target project ID is retained so the normal
        If-Match and client cache contracts continue to apply; object IDs,
        plan version, groups and species IDs come from the manifest unchanged.
        """

        del filename  # The canonical source name is part of the signed payload.
        parsed = parse_release_bundle(content)
        project = self.repository.get(project_id)
        if project.plan is not None:
            raise ValueError(
                "Нельзя заменить исходный DXF после открытия ручной схемы. Создайте новый проект."
            )
        if not parsed.source_content:
            raise ValueError("Пакет выпуска не содержит исходного DXF")
        imported_drawings = [
            ImportedDrawing(
                path=parsed.source_path,
                source=parsed.source_content,
                imported=self.dxf_reader.read(
                    parsed.source_path, parsed.source_content
                ),
            ),
            *[
                ImportedDrawing(
                    path=path,
                    source=drawing,
                    imported=self.dxf_reader.read(path, drawing),
                )
                for path, drawing in sorted(parsed.source_components.items())
            ],
        ]
        imported = (
            imported_drawings[0].imported
            if len(imported_drawings) == 1
            else compose_dxf_imports(imported_drawings).imported
        )
        editable = parsed.editable and imported.preview_provenance is None
        manifest = parsed.manifest
        project_payload = manifest.get("project")
        project_meta = project_payload if isinstance(project_payload, dict) else {}
        source_payload = manifest.get("source")
        source_meta = source_payload if isinstance(source_payload, dict) else {}
        if project_meta.get("name"):
            project.name = str(project_meta["name"])

        project.source_file = SourceFile(
            name=parsed.source_filename,
            content_sha256=sha256(parsed.source_content).hexdigest(),
            size=len(parsed.source_content),
            imported_at=self.now().isoformat(),
            dxf_version=imported.dxf_version,
            units=imported.units,
            units_assumed=imported.units_assumed,
            entity_count=imported.entity_count,
            bounds=imported.bounds,
            warnings=list(imported.warnings),
            preview_provenance=imported.preview_provenance,
            prepared_provenance=source_meta.get("prepared_provenance"),
        )
        provenance = project.source_file.prepared_provenance
        if provenance is not None:
            if (
                provenance.entry != parsed.source_path
                or provenance.source_sha256
                != project.source_file.content_sha256
            ):
                raise ValueError(
                    "Происхождение подготовленного исходника не совпадает "
                    "с его содержимым"
                )
            source_files = {
                parsed.source_path: parsed.source_content,
                **parsed.source_components,
            }
            expected_drawings = {item.path: item for item in provenance.drawings}
            if provenance.drawings and (
                len(expected_drawings) != len(provenance.drawings)
                or set(source_files) != set(expected_drawings)
                or any(
                    len(source_files[path]) != item.source_bytes
                    or sha256(source_files[path]).hexdigest()
                    != item.source_sha256
                    for path, item in expected_drawings.items()
                )
            ):
                raise ValueError(
                    "Комплект исходных DXF не совпадает с его "
                    "происхождением"
                )
        project.layers = imported.layers
        mappings = manifest.get("layer_mappings")
        if isinstance(mappings, list):
            by_source = {
                str(item.get("source_name")): item
                for item in mappings
                if isinstance(item, dict) and item.get("source_name")
            }
            for layer in project.layers:
                item = by_source.get(layer.source_name)
                if not item:
                    continue
                kind = item.get("kind")
                try:
                    if kind is not None:
                        layer.mapped_kind = LayerKind(kind)
                except ValueError as error:
                    raise ValueError(
                        "Manifest содержит неизвестный тип слоя"
                    ) from error
                if "visible" in item:
                    layer.visible = bool(item["visible"])
                if (
                    layer.mapped_kind == LayerKind.UTILITY
                    and item.get("utility_context") is not None
                ):
                    layer.utility_context = UtilityContext.model_validate(
                        item["utility_context"]
                    )
                layer.utility_axis_bindings = [
                    UtilityAxisBinding.model_validate(binding)
                    for binding in item.get("utility_axis_bindings", [])
                ]
        project.coordinate_reference = imported.coordinate_reference
        coordinate_payload = project_meta.get("coordinate_reference")
        if coordinate_payload is not None:
            try:
                project.coordinate_reference = type(
                    project.coordinate_reference
                ).model_validate(coordinate_payload)
            except Exception as error:
                raise ValueError(
                    "Система координат в manifest имеет неверную структуру"
                ) from error

        # A package without the inline source or complete plan is an
        # intentional read-only compatibility fallback. Do not graft a
        # partially reconstructed plan onto a derived DXF.
        project.import_status = ImportStatus(
            mode=ImportMode.RELEASE_BUNDLE
            if editable
            else ImportMode.PLAIN_DXF_FALLBACK,
            editability=ImportEditability.EDITABLE
            if editable
            else ImportEditability.READ_ONLY,
            release_id=str(manifest.get("release_id"))
            if manifest.get("release_id")
            else None,
            message=(
                "Ревизия восстановлена из полного ZIP-пакета и доступна для редактирования."
                if editable
                else parsed.read_only_reason or "Пакет открыт только для просмотра."
            ),
        )
        review_payload = project_meta.get("source_review")
        project.source_review = SourceReview.model_validate(review_payload) if review_payload is not None else None
        if imported.preview_provenance is not None:
            project.import_status = cad_preview_status()
        if not editable:
            project.source_file.warnings.append(project.import_status.message)
        validate_axis_bindings(
            project, imported.geometry.feature_collection.get("features", [])
        )
        project.geometry_version = int(project_meta.get("geometry_version", 0) or 0)
        project.planting_zones = (
            [zone.model_copy(deep=True) for zone in parsed.planting_zones]
            if editable
            else []
        )
        project.plan = (
            parsed.plan.model_copy(deep=True)
            if editable and parsed.plan is not None
            else None
        )
        if parsed.geometry is not None and editable:
            project.geometry = parsed.geometry.model_copy(deep=True)
            utility_contexts = utility_contexts_by_layer(project.layers)
            for feature in project.geometry.feature_collection.get("features", []):
                assign_utility_context(
                    feature.setdefault("properties", {}), utility_contexts
                )
            project.source_geometry = None
            project.map_ready = True
        else:
            project.geometry = None
            project.source_geometry = imported.geometry
            project.map_ready = False
        project.site_area_m2 = (
            parsed.geometry.site_area_m2
            if editable and parsed.geometry is not None
            else None
        )
        project.planning_area_m2 = (
            parsed.geometry.planning_area_m2
            if editable and parsed.geometry is not None
            else None
        )
        project.allowed_area_m2 = (
            parsed.geometry.allowed_area_m2
            if editable and parsed.geometry is not None
            else None
        )
        project.status = (
            ProjectStatus.EDITING
            if editable and parsed.plan is not None
            else ProjectStatus.IMPORTED
        )
        saved = self.repository.save(
            project,
            source=parsed.source_content,
            source_components=parsed.source_components,
        )
        self.invalidate_spatial(project.id)
        self.history.clear(project.id)
        return saved

    def save_mappings(self, project_id: str, mappings: list[LayerMapping]) -> Project:
        # Reject an invalid binding without mutating even an in-memory repository.
        project = self.repository.get(project_id).model_copy(deep=True)
        mapping_by_id = {item.layer_id: item for item in mappings}
        if len(mapping_by_id) != len(mappings):
            raise ValueError("Слой указан в сопоставлении более одного раза")
        known_layers = {layer.id for layer in project.layers}
        if any(
            item.utility_axis_bindings and item.layer_id not in known_layers
            for item in mappings
        ):
            raise ValueError("Привязка оси ссылается на отсутствующий слой")
        meaning_changed = False
        axis_bindings_changed = False
        for layer in project.layers:
            if layer.id in mapping_by_id:
                mapping = mapping_by_id[layer.id]
                meaning_changed = meaning_changed or layer.mapped_kind != mapping.kind
                context = layer.utility_context
                if mapping.kind != LayerKind.UTILITY:
                    context = None
                elif "utility_context" in mapping.model_fields_set:
                    context = mapping.utility_context
                meaning_changed = meaning_changed or context != layer.utility_context
                layer.utility_context = context
                bindings = layer.utility_axis_bindings
                if mapping.kind != LayerKind.UTILITY:
                    bindings = []
                elif "utility_axis_bindings" in mapping.model_fields_set:
                    bindings = mapping.utility_axis_bindings
                binding_changed = bindings != layer.utility_axis_bindings
                meaning_changed = meaning_changed or binding_changed
                axis_bindings_changed = axis_bindings_changed or binding_changed
                layer.utility_axis_bindings = bindings
                layer.mapped_kind = mapping.kind
                layer.visible = mapping.visible
        missing = [
            layer.source_name
            for layer in project.layers
            if layer.required
            and (
                layer.id not in mapping_by_id
                or mapping_by_id[layer.id].kind.value == "ignore"
            )
        ]
        if missing:
            raise ValueError(f"Не сопоставлены обязательные слои: {', '.join(missing)}")
        if axis_bindings_changed and any(
            layer.utility_axis_bindings for layer in project.layers
        ):
            source = self.repository.get_source(project.id)
            source_file = project.source_file
            if (
                source_file is None
                or source is None
                or sha256(source).hexdigest() != source_file.content_sha256
            ):
                raise ValueError(
                    "Исходный DXF отсутствует или его хеш изменился; подтверждение оси невозможно"
                )
            # Re-read the immutable input for this explicit interpretation step.
            # A cached calculated snapshot or a submitted map feature is not proof.
            original = self.dxf_reader.read(source_file.name, source)
            if original.preview_provenance is not None:
                raise ValueError(
                    "Производный CAD preview нельзя подтвердить как исходную ось"
                )
            validate_axis_bindings(
                project, original.geometry.feature_collection.get("features", [])
            )
            # Rebuild from the same verified source that resolved the binding.
            # Old calculated/viewport snapshots may lack reader provenance or
            # contain rounded/clipped derivatives of this exact source entity.
            project.source_geometry = original.geometry
        if meaning_changed:
            # The calculated snapshot is a product of the previous mapping.
            # Retaining it after a building/road/etc. changes meaning would
            # let manual placement rely on no-longer-authoritative setbacks.
            project.map_ready = False
            project.site_area_m2 = None
            project.planning_area_m2 = None
            project.allowed_area_m2 = None
            if (project.plan is not None or project.source_review is not None) and project.source_geometry is None:
                if project.geometry is None:
                    raise ValueError("Сохранённая карта недоступна для уточнения слоёв")
                source_features = [
                    deepcopy(feature)
                    for feature in project.geometry.feature_collection.get(
                        "features", []
                    )
                    if feature.get("properties", {}).get("source_layer")
                ]
                project.source_geometry = GeometrySnapshot(
                    vertical_primitives=project.geometry.vertical_primitives,
                    feature_collection={
                        "type": "FeatureCollection",
                        "features": source_features,
                    }
                )
            project.geometry = None
            project.geometry_version += 1
            if project.plan is None and project.source_review is None:
                project.planting_zones = []
            if project.source_review is not None:
                project = open_source_editor(project)
        if project.geometry is None and project.plan is None:
            project.status = ProjectStatus.MAPPED
        saved = self.repository.save(project)
        if meaning_changed:
            self.invalidate_spatial(project.id)
        return saved
