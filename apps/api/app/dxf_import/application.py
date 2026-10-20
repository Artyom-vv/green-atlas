"""Сценарии импорта и назначения слоёв: допуск исходника, адресный просмотр и сохранение
проекта через порты.
"""

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from app.cad_bridge import CadSnapshot
from app.cad_bridge.compiler import compile_live_document, compile_live_document_file
from app.cad_bridge.face_inputs import FACE_INPUT_NAME, FaceInputPublication
from app.cad_bridge.provider import (
    _build_project_from_admitted_snapshot,
    build_project_from_snapshot,
)
from app.dxf_import import limits
from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.contracts import (
    ImportEditability,
    ImportMode,
    NativeAreaPreview,
)
from app.dxf_import.layers.categories import NETWORK_TYPES
from app.dxf_import.layers.contracts import LayerKind, LayerMapping
from app.dxf_import.ports import DxfReaderPort, ImportProjectRepository
from app.dxf_import.review.contracts import (
    SourceAreaGroupRequest,
    SourceObjectContext,
    SourceObjectDecisionRequest,
    SourceObjectReviewPage,
    SourceReadIssues,
)
from app.dxf_import.review.native_areas import (
    append_accepted_areas,
    decide_native_area,
    preview_native_area,
    review_records,
)
from app.dxf_import.source.admission import require_usable_site_boundary
from app.dxf_import.source.editor_source import (
    is_native_source_editor,
    open_source_editor,
    source_editor_review,
)
from app.geometry.contracts import GeometrySnapshot
from app.geometry.networks.axis_bindings import validate_axis_bindings
from app.geometry.networks.utility_contracts import (
    EVIDENCE_CONTEXT_FIELDS,
    SEMANTIC_CONTEXT_FIELDS,
    UtilityContext,
)
from app.history.ports import ProjectHistoryResetPort
from app.native_query.live.live_client import LiveSession
from app.projects.contracts import Project, ProjectStatus
from app.shared.failures import DomainFailure, FailureReason


class ImportApplication:
    def __init__(
        self,
        *,
        repository: ImportProjectRepository,
        dxf_reader: DxfReaderPort,
        history: ProjectHistoryResetPort,
        invalidate_spatial: Callable[[str], None],
        now: Callable[[], datetime],
        recheck_plan: Callable[[Project], bool] | None = None,
        preserve_local_geometry: Callable[[Project], object] | None = None,
    ) -> None:
        self.repository = repository
        self.dxf_reader = dxf_reader
        self.history = history
        self.invalidate_spatial = invalidate_spatial
        self.now = now
        self.recheck_plan = recheck_plan
        self.preserve_local_geometry = preserve_local_geometry

    def bind_native_session(self, project: Project, session: LiveSession) -> Project:
        """Завершить передачу через узкую CAS-привязку сохранённого CAD-сеанса."""
        return self.repository.bind_native_session(project, session)

    def open_editor(self, project_id: str) -> Project:
        project = self.repository.get(project_id, lightweight=True)
        if project.map_ready and project.geometry is not None:
            # Общая политика сохраняет допуск исходника и запрет read-only.
            # Уже открытая карта не требует полного графа или новой ревизии.
            return open_source_editor(project)
        project = self.repository.get(project_id)
        opened = open_source_editor(project)
        if opened is project:
            return project
        saved = self.repository.save(opened)
        self.invalidate_spatial(project.id)
        return saved

    def accept_partial_geometry(self, project_id: str, source_sha256: str) -> Project:
        project = self.repository.get(project_id).model_copy(deep=False)
        source = project.source_file
        if source is None or source.content_sha256 != source_sha256:
            raise DomainFailure(FailureReason.SOURCE_REVIEW_CHANGED)
        if project.import_status.editability == ImportEditability.READ_ONLY:
            raise DomainFailure(FailureReason.SOURCE_READ_ONLY)
        if source.accept_partial_geometry:
            return project
        project.source_file = source.model_copy(
            update={"accept_partial_geometry": True}
        )
        # Меняется только согласие; слои, неизменяемая геометрия, зоны и план сохраняются.
        return self.repository.save(project)

    def native_area_preview(
        self, project_id: str, proposal_id: str
    ) -> NativeAreaPreview:
        project = self.repository.get(project_id, lightweight=True)
        source = self.repository.get_source(project_id)
        if source is None:
            raise DomainFailure(FailureReason.NATIVE_SNAPSHOT_MISSING)
        return preview_native_area(project, source, proposal_id)

    def source_object_review(
        self,
        project_id: str,
        *,
        layer: str | None = None,
        offset: int = 0,
        limit: int = 30,
    ) -> SourceObjectReviewPage:
        from app.dxf_import.review.objects import review_objects

        return review_objects(
            self.repository.get(project_id), layer=layer, offset=offset, limit=limit
        )

    def source_object_context(
        self, project_id: str, route: str, scale: float
    ) -> SourceObjectContext:
        from app.dxf_import.review.context import object_context

        return object_context(self.repository.get(project_id), route, scale=scale)

    def source_read_issues(self, project_id: str) -> SourceReadIssues:
        from app.dxf_import.review.issues import source_read_issues

        return source_read_issues(
            self.repository.get(project_id, lightweight=True),
            self.repository.get_source(project_id),
        )

    def save_area_group(
        self, current: Project, request: SourceAreaGroupRequest
    ) -> Project:
        from app.dxf_import.review.groups import accept_group

        saved = self.repository.save(open_source_editor(accept_group(current, request)))
        self.invalidate_spatial(current.id)
        self.history.clear(current.id)
        return saved

    def save_native_face_decision(self, current: Project, changed: Project) -> Project:
        if changed is current:
            return current
        saved = self.repository.save(open_source_editor(changed))
        self.invalidate_spatial(current.id)
        self.history.clear(current.id)
        return saved

    def remove_area_group(self, project_id: str, group_id: str) -> Project:
        from app.dxf_import.review.groups import remove_group

        saved = self.repository.save(
            open_source_editor(remove_group(self.repository.get(project_id), group_id))
        )
        self.invalidate_spatial(project_id)
        self.history.clear(project_id)
        return saved

    def decide_source_object(
        self, project_id: str, request: SourceObjectDecisionRequest
    ) -> Project:
        from app.dxf_import.review.objects import decide_object

        current = self.repository.get(project_id)
        changed = decide_object(current, request)
        if changed is current:
            return current
        saved = self.repository.save(open_source_editor(changed))
        self.invalidate_spatial(project_id)
        self.history.clear(project_id)
        return saved

    def decide_native_area(
        self,
        project_id: str,
        *,
        source_sha256: str,
        proposal_id: str,
        proposal_sha256: str,
        decision: Literal["accepted", "rejected"],
    ) -> Project:
        current = self.repository.get(project_id)
        source = self.repository.get_source(project_id)
        if source is None:
            raise DomainFailure(FailureReason.NATIVE_SNAPSHOT_MISSING)
        changed = decide_native_area(
            current,
            source,
            source_sha256=source_sha256,
            proposal_id=proposal_id,
            proposal_sha256=proposal_sha256,
            decision=decision,
        )
        if changed is current:
            return current
        opened = open_source_editor(changed)
        saved = self.repository.save(opened)
        self.invalidate_spatial(project_id)
        self.history.clear(project_id)
        return saved

    def import_autocad_live(
        self,
        project_id: str,
        filename: str,
        content: bytes | bytearray,
        *,
        autocad_version: str,
        target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
        capacity: SourceGeometryCapacity | None = None,
    ) -> Project:
        """Открывать захваченную native-геометрию без промежуточного DXF и повторного
        чтения XREF.
        """
        project = self.repository.get(project_id)
        if project.source_file is not None or project.plan is not None:
            raise DomainFailure(FailureReason.NATIVE_SNAPSHOT_REQUIRES_NEW_PROJECT)
        if not content or len(content) > limits.MAX_CAD_SNAPSHOT_BYTES:
            raise DomainFailure(FailureReason.NATIVE_SNAPSHOT_SIZE_INVALID)
        snapshot = compile_live_document(
            content,
            autocad_version=autocad_version,
            target=target,
        )
        project = self._project_from_live_snapshot(
            project,
            filename,
            snapshot,
            source_sha256=sha256(content).hexdigest(),
            source_size=len(content),
            capacity=capacity,
        )
        del snapshot
        project = open_source_editor(project)
        saved = self.repository.save(project, source=content)
        self.invalidate_spatial(project.id)
        self.history.clear(project.id)
        return saved

    def import_autocad_live_file(
        self,
        project_id: str,
        filename: str,
        source_path: Path,
        source_sha256: str,
        *,
        autocad_version: str,
        target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
        capacity: SourceGeometryCapacity | None = None,
        native_capture_root: Path | None = None,
    ) -> Project:
        """Не удерживать закодированные байты при компиляции; повторно проверять их до
        атомарного сохранения.
        """
        project = self.repository.get(project_id)
        if project.source_file is not None or project.plan is not None:
            raise DomainFailure(FailureReason.NATIVE_SNAPSHOT_REQUIRES_NEW_PROJECT)
        if not 0 < source_path.stat().st_size <= limits.MAX_CAD_SNAPSHOT_BYTES:
            raise DomainFailure(FailureReason.NATIVE_SNAPSHOT_SIZE_INVALID)
        project, source_size = self._project_from_live_file(
            project,
            filename,
            source_path,
            source_sha256=source_sha256,
            autocad_version=autocad_version,
            target=target,
            capacity=capacity,
            face_inputs=FaceInputPublication(native_capture_root / FACE_INPUT_NAME)
            if native_capture_root is not None
            else None,
        )
        if native_capture_root is not None:
            from app.native_query.capture.semantic_evidence import attach_semantics

            attach_semantics(native_capture_root, source_sha256, project.layers)
        # Этот проект создан импортером и ещё не опубликован. Передаём его
        # объекты редактору без второй коллекции Feature и их свойств.
        project = open_source_editor(project, consume_owned=True)
        # Файл мог измениться после допуска. Проверяем именно байты, которые сохранит
        # транзакционный репозиторий, без второго импорта.
        if source_path.stat().st_size != source_size:
            raise DomainFailure(FailureReason.NATIVE_SNAPSHOT_CHANGED)
        if native_capture_root is not None:
            # Полный захват уже хранится и проверяется в CAD-архиве проекта.
            # Крупный JSON не дублируется в RAM и единичном SQLite BLOB.
            saved = self.repository.save_retained_source(project, native_capture_root)
        else:
            content = source_path.read_bytes()
            if (
                len(content) != source_size
                or sha256(content).hexdigest() != source_sha256
            ):
                raise DomainFailure(FailureReason.NATIVE_SNAPSHOT_CHANGED)
            saved = self.repository.save(project, source=content)
        self.invalidate_spatial(project.id)
        self.history.clear(project.id)
        return saved

    def _project_from_live_file(
        self,
        project: Project,
        filename: str,
        source_path: Path,
        *,
        source_sha256: str,
        autocad_version: str,
        target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
        capacity: SourceGeometryCapacity | None,
        face_inputs: FaceInputPublication | None = None,
    ) -> tuple[Project, int]:
        """Допустить файл и потребить свежий снимок до возврата проекта.

        Компилятор проверяет реальные байты исходника, геометрию и связи,
        затем сам вычисляет подписи. Снимок не выходит из этого вызова:
        повторный проход по только что вычисленным подписям не нужен.
        Внешние снимки продолжают проходить общий проверяющий вход.
        """
        snapshot, source_size = compile_live_document_file(
            source_path,
            expected_source_sha256=source_sha256,
            autocad_version=autocad_version,
            target=target,
            face_inputs=face_inputs,
        )
        project = _build_project_from_admitted_snapshot(
            project,
            snapshot,
            filename=filename,
            imported_at=self.now().isoformat(),
            source_sha256=source_sha256,
            source_size=source_size,
            capacity=capacity,
            consume_owned=True,
        )
        self._finish_live_project(project, snapshot)
        if face_inputs is not None and project.source_file is not None:
            provenance = project.source_file.cad_snapshot_provenance
            if provenance is not None:
                # Квитанция каталога сохраняется атомарно с допущенным исходником.
                provenance.face_inputs_sha256 = face_inputs.sha256
        return project, source_size

    def _project_from_live_snapshot(
        self,
        project: Project,
        filename: str,
        snapshot: CadSnapshot,
        *,
        source_sha256: str,
        source_size: int,
        capacity: SourceGeometryCapacity | None,
        consume_owned: bool = False,
    ) -> Project:
        project = build_project_from_snapshot(
            project,
            snapshot,
            filename=filename,
            imported_at=self.now().isoformat(),
            source_sha256=source_sha256,
            source_size=source_size,
            capacity=capacity,
            consume_owned=consume_owned,
        )
        self._finish_live_project(project, snapshot)
        return project

    @staticmethod
    def _finish_live_project(project: Project, snapshot: CadSnapshot) -> None:
        geometry = project.source_geometry
        if geometry is None or not geometry.feature_collection.get("features"):
            raise DomainFailure(FailureReason.NATIVE_GEOMETRY_EMPTY)
        if project.source_file is not None:
            project.source_file.native_area_proposals = review_records(
                snapshot, geometry.feature_collection.get("features", [])
            )

    def save_mappings(self, project_id: str, mappings: list[LayerMapping]) -> Project:
        # Некорректная привязка отклоняется до мутации, включая репозиторий в памяти.
        current = self.repository.get(project_id, lightweight=True)
        initial_import = (
            current.import_status.mode == ImportMode.AUTOCAD_LIVE
            and current.source_file is not None
            and current.source_file.native_session is not None
            and current.geometry is None
            and current.plan is None
            and current.source_review is None
            and not any(layer.utility_axis_bindings for layer in current.layers)
            and not any(item.utility_axis_bindings for item in mappings)
        )
        source_editor = (
            is_native_source_editor(current)
            and current.plan is None
            and not any(layer.utility_axis_bindings for layer in current.layers)
            and not any(item.utility_axis_bindings for item in mappings)
        )
        if not (initial_import or source_editor):
            current = self.repository.get(project_id)
        project = current.model_copy(
            update={
                "layers": [layer.model_copy(deep=True) for layer in current.layers],
            }
        )
        if project.import_status.editability == ImportEditability.READ_ONLY:
            raise DomainFailure(FailureReason.SOURCE_READ_ONLY)
        mapping_by_id = {item.layer_id: item for item in mappings}
        if len(mapping_by_id) != len(mappings):
            raise DomainFailure(FailureReason.LAYER_MAPPING_DUPLICATE)
        known_layers = {layer.id for layer in project.layers}
        if any(item.layer_id not in known_layers for item in mappings):
            raise DomainFailure(FailureReason.AXIS_LAYER_MISSING)
        geometry_changed = False
        calculation_changed = False
        evidence_changed = False
        axis_bindings_changed = False
        native_preparation = (
            current.source_file is not None
            and current.source_file.native_session is not None
        )
        for layer in project.layers:
            if layer.id in mapping_by_id:
                mapping = mapping_by_id[layer.id]
                geometry_changed = geometry_changed or layer.mapped_kind != mapping.kind
                category = (
                    mapping.category
                    if "category" in mapping.model_fields_set
                    else (layer.category if layer.mapped_kind == mapping.kind else None)
                )
                geometry_changed = geometry_changed or layer.category != category
                layer.category = category
                # Прежняя форма не содержит подтверждения; явный native null означает неизвестное
                # участие. Подмена null на true изменила бы физическое основание подготовки.
                confirmed = (
                    mapping.confirmed
                    if native_preparation and "confirmed" in mapping.model_fields_set
                    else mapping.confirmed is not False
                )
                geometry_changed = geometry_changed or (
                    layer.mapping_confirmed != confirmed
                    if native_preparation
                    else (layer.mapping_confirmed is not False) != confirmed
                )
                context = layer.utility_context
                if mapping.kind != LayerKind.UTILITY:
                    context = None
                elif "utility_context" in mapping.model_fields_set:
                    context = mapping.utility_context
                if mapping.kind == LayerKind.UTILITY and category in NETWORK_TYPES:
                    if context is None or context.network_type == "unknown":
                        context = (context or UtilityContext()).model_copy(
                            update={
                                "network_type": NETWORK_TYPES[category],
                                "review_status": "unconfirmed",
                            }
                        )
                # Проверяем итоговую интерпретацию: прежний клиент может изменить сеть,
                # не передав её уже известную категорию.
                LayerMapping(
                    layer_id=layer.id,
                    kind=mapping.kind,
                    category=category,
                    confirmed=mapping.confirmed,
                    utility_context=context,
                )
                previous = layer.utility_context
                calculation_changed = calculation_changed or (
                    (
                        previous.model_dump(
                            mode="json", include=set(SEMANTIC_CONTEXT_FIELDS)
                        )
                        if previous
                        else None
                    )
                    != (
                        context.model_dump(
                            mode="json", include=set(SEMANTIC_CONTEXT_FIELDS)
                        )
                        if context
                        else None
                    )
                )
                evidence_changed = evidence_changed or (
                    (
                        previous.model_dump(
                            mode="json", include=set(EVIDENCE_CONTEXT_FIELDS)
                        )
                        if previous
                        else None
                    )
                    != (
                        context.model_dump(
                            mode="json", include=set(EVIDENCE_CONTEXT_FIELDS)
                        )
                        if context
                        else None
                    )
                )
                layer.utility_context = context
                bindings = layer.utility_axis_bindings
                if mapping.kind != LayerKind.UTILITY:
                    bindings = []
                elif "utility_axis_bindings" in mapping.model_fields_set:
                    bindings = mapping.utility_axis_bindings
                binding_changed = bindings != layer.utility_axis_bindings
                geometry_changed = geometry_changed or binding_changed
                axis_bindings_changed = axis_bindings_changed or binding_changed
                layer.utility_axis_bindings = bindings
                layer.mapped_kind = mapping.kind
                layer.mapping_confirmed = confirmed
                layer.visible = mapping.visible
        if initial_import or source_editor:
            # Проверяем прежнюю геометрию выбранной границы, не загружая всю карту.
            # Выборка принадлежит проверке и не подменяет сохранённый исходник.
            boundary_names = {
                layer.source_name
                for layer in project.layers
                if layer.mapped_kind == LayerKind.SITE_BORDER
            }
            boundary_features = (
                self.repository.read_source_features(
                    project.id,
                    boundary_names,
                    expected_version=current.state_version,
                    geometry_field="geometry" if source_editor else "source_geometry",
                )
                if boundary_names
                else []
            )
            require_usable_site_boundary(project, source_features=boundary_features)
        else:
            require_usable_site_boundary(project)
        # Частичная запись сохраняет прежние назначения. Проверяем итоговый проект,
        # включая обязательные слои, отсутствующие в PUT.
        missing = [
            layer.source_name
            for layer in project.layers
            if layer.required and layer.mapped_kind in {None, LayerKind.IGNORE}
        ]
        if missing:
            raise DomainFailure(
                FailureReason.REQUIRED_LAYERS_UNMAPPED, {"layer_names": missing}
            )
        # Сохраняем определённые предложения модели, оставляя сомнительные слои ожидающими.
        # Допуск расчёта и подготовки геометрии всё равно требует подтверждения.
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
                raise DomainFailure(FailureReason.AXIS_SOURCE_CHANGED)
            # Для явной интерпретации перечитываем неизменяемый исходник. Кеш расчёта
            # или присланный объект карты не является доказательством.
            if project.import_status.mode == ImportMode.AUTOCAD_LIVE:
                provenance = source_file.cad_snapshot_provenance
                if provenance is None or provenance.live_capture is None:
                    raise DomainFailure(
                        FailureReason.NATIVE_SNAPSHOT_PROVENANCE_UNCONFIRMED
                    )
                snapshot = compile_live_document(
                    source,
                    autocad_version=provenance.autocad_version,
                    target=provenance.target,
                )
                native_project = build_project_from_snapshot(
                    project,
                    snapshot,
                    filename=source_file.name,
                    imported_at=source_file.imported_at,
                    source_sha256=sha256(source).hexdigest(),
                    source_size=len(source),
                )
                original_geometry = native_project.source_geometry
                if original_geometry is None:
                    raise DomainFailure(FailureReason.SOURCE_MAP_MISSING)
                if source_file.native_area_proposals:
                    append_accepted_areas(
                        snapshot,
                        source_file.native_area_proposals,
                        original_geometry.feature_collection["features"],
                    )
            else:
                original = self.dxf_reader.read(source_file.name, source)
                if original.preview_provenance is not None:
                    raise DomainFailure(FailureReason.AXIS_PREVIEW_INVALID)
                original_geometry = original.geometry
            validate_axis_bindings(
                project, original_geometry.feature_collection.get("features", [])
            )
            # Пересобираем по тому же проверенному исходнику, который разрешил привязку.
            # Старые расчётные снимки и карта могут содержать округлённые производные без происхождения.
            project.source_geometry = original_geometry
        if (
            not geometry_changed
            and (calculation_changed or evidence_changed)
            and self.preserve_local_geometry is not None
        ):
            # Ключи v1 включают старые сетевые свидетельства. До их изменения принимаем
            # только точный проверенный исходный ключ без обращения к CAD.
            self.preserve_local_geometry(current)
        if geometry_changed or calculation_changed or evidence_changed:
            if project.plan is not None:
                # Повторная проверка меняет состояния посадок и замечания, сохраняя CAD-карту.
                project.plan = project.plan.model_copy(deep=True)
                project.plan.validation_basis = None
        if geometry_changed:
            # Расчётный снимок получен по прежним назначениям. После изменения смысла здания,
            # дороги или другого объекта его отступы больше не являются действующим основанием.
            project.map_ready = False
            project.site_area_m2 = None
            project.planning_area_m2 = None
            project.allowed_area_m2 = None
            if source_editor:
                # Назначения принадлежат ревизии проекта, исходный CAD-граф —
                # неизменяемому захвату. Чтение накладывает роли, запись меняет
                # только метаданные и сохраняет прежнюю инвалидацию расчёта.
                assert project.geometry is not None
                project.geometry = GeometrySnapshot(
                    vertical_primitives=project.geometry.vertical_primitives,
                    feature_collection=project.geometry.feature_collection,
                )
                project.source_review = source_editor_review(project)
                project.map_ready = True
                project.geometry_version += 2
            elif (
                project.plan is not None or project.source_review is not None
            ) and project.source_geometry is None:
                if project.geometry is None:
                    raise DomainFailure(FailureReason.SOURCE_MAP_MISSING)
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
                    },
                )
            if not source_editor:
                project.geometry = None
                project.geometry_version += 1
                if project.plan is None and project.source_review is None:
                    project.planting_zones = []
                if project.source_review is not None:
                    project = open_source_editor(project)
        elif (calculation_changed or evidence_changed) and project.plan is not None:
            # Обратный вызов проверяет локальную доступность. Отсутствующая или повреждённая
            # подготовка оставляет прежние замечания видимыми с неизвестным основанием.
            if self.recheck_plan is not None:
                if not self.recheck_plan(project):
                    project.plan = (
                        current.plan.model_copy(deep=True) if current.plan else None
                    )
                    if project.plan is not None:
                        project.plan.validation_basis = None
        if project.geometry is None and project.plan is None:
            project.status = ProjectStatus.MAPPED
        saved = (
            self.repository.save_import_mappings(project)
            if initial_import
            else self.repository.save_import_mappings(project, source_editor=True)
            if source_editor
            else self.repository.save(project)
        )
        if geometry_changed or calculation_changed or evidence_changed:
            self.invalidate_spatial(project.id)
        return saved
