"""Сценарии выпуска с проверкой готовности, устойчивой идентичностью и атомарной
публикацией артефактов.
"""

from collections.abc import Callable
from hashlib import sha256

from app.dxf_import.contracts import ImportMode
from app.exporting.cad_contracts import CadRelease
from app.exporting.contracts import (
    ExportArtifact,
    ReleaseArtifact,
    ReleaseCreateRequest,
    ReleasePackage,
)
from app.exporting.evidence import UnavailableReleaseEvidence
from app.exporting.ports import CadWriterPort, DxfWriterPort, ExportProjectRepository
from app.geometry.ports import GeometryEnginePort, GeometryRuleTracePort
from app.projects.contracts import Project
from app.releases.service import build_release, release_identity, zip_release_entries
from app.scene.contracts import SceneSnapshot
from app.shared.failures import (
    DomainFailure,
    FailureReason,
    ReleaseCheck,
    ResourceNotFound,
)
from app.validation.application import PlanValidation
from app.validation.networks import release_network_issues


class ExportApplication:
    def __init__(
        self,
        *,
        repository: ExportProjectRepository,
        writer: DxfWriterPort,
        scene: Callable[[Project, int], SceneSnapshot],
        geometry: GeometryEnginePort | None = None,
        cad_writer: CadWriterPort | None = None,
        validation: PlanValidation | None = None,
    ) -> None:
        self.repository = repository
        self.writer = writer
        self.scene = scene
        self.geometry = geometry
        self.cad_writer = cad_writer
        self.validation = validation

    def _cad(self, project: Project) -> CadRelease:
        if self.cad_writer is None:
            raise DomainFailure(FailureReason.CAD_RELEASE_MODULE_MISSING)
        return self.cad_writer.create(project)

    def _source_components(
        self, project: Project, source_content: bytes
    ) -> dict[str, bytes]:
        if project.import_status.mode == ImportMode.AUTOCAD_LIVE:
            raise DomainFailure(FailureReason.LIVE_DXF_EXPORT_UNAVAILABLE)
        components = self.repository.get_source_components(project.id)
        source_file = project.source_file
        provenance = source_file.prepared_provenance if source_file else None
        if provenance is None or not provenance.drawings:
            if components:
                raise DomainFailure(FailureReason.SOURCE_COMPONENTS_UNVERIFIED)
            return components
        files = {provenance.entry: source_content, **components}
        expected = {item.path: item for item in provenance.drawings}
        if (
            len(expected) != len(provenance.drawings)
            or set(files) != set(expected)
            or any(
                len(files[path]) != item.source_bytes
                or sha256(files[path]).hexdigest() != item.source_sha256
                for path, item in expected.items()
            )
        ):
            raise DomainFailure(FailureReason.SOURCE_COMPONENTS_MISMATCH)
        return components

    def export(self, project_id: str) -> ExportArtifact:
        project = self.repository.get(project_id)
        if project.import_status.mode == ImportMode.AUTOCAD_LIVE:
            cad = self._cad(project)
            content = zip_release_entries(cad.files)
            artifact = ExportArtifact(
                filename="green-atlas-cad.zip",
                status="ready",
                size=len(content),
                download_url="",
                kind="cad",
                media_type="application/zip",
            )
            artifact.download_url = (
                f"/api/projects/{project.id}/exports/{artifact.id}/download"
            )
            self.repository.publish_export(project, artifact.id, content)
            return artifact
        source_content = self.repository.get_source(project.id)
        if source_content is None:
            raise DomainFailure(FailureReason.DXF_EXPORT_SOURCE_MISSING)
        source_components = self._source_components(project, source_content)
        artifact, content = self.writer.create(
            project,
            source_content,
            source_components,
        )
        artifact.download_url = (
            f"/api/projects/{project.id}/exports/{artifact.id}/download"
        )
        # Байты и видимое состояние фиксируются вместе. Последовательные save_export
        # и save(project) оставили бы недоступный файл, если другая вкладка
        # изменит проект между этими записями.
        self.repository.publish_export(project, artifact.id, content)
        return artifact

    def create_release(
        self, project_id: str, request: ReleaseCreateRequest
    ) -> ReleasePackage:
        from app.projects.resource_scope import project_resource_scope

        with project_resource_scope(self.repository, project_id):
            return self._create_release(project_id, request)

    def _create_release(
        self, project_id: str, request: ReleaseCreateRequest
    ) -> ReleasePackage:
        project = self.repository.get(project_id)
        if project.plan is None:
            raise DomainFailure(FailureReason.PLAN_MISSING)
        if not project.plan.objects:
            raise DomainFailure(FailureReason.PLANTING_REQUIRED)
        if project.source_file is None:
            raise DomainFailure(FailureReason.DXF_RELEASE_SOURCE_MISSING)
        evidence: GeometryRuleTracePort | None = self.geometry
        if project.import_status.mode == ImportMode.AUTOCAD_LIVE:
            load = getattr(type(evidence), "prepared_engine", None)
            if load is not None:
                evidence = load(evidence, project) or UnavailableReleaseEvidence()
            elif evidence is None:
                evidence = UnavailableReleaseEvidence()
            if (
                isinstance(evidence, UnavailableReleaseEvidence)
                and request.mode == "final"
            ):
                raise DomainFailure(FailureReason.RELEASE_CHECKS_UNAVAILABLE)
        if request.mode == "final":
            if project.source_review is not None:
                raise DomainFailure(FailureReason.RELEASE_SOURCE_REVIEW_PENDING)
            error_count = sum(
                issue.severity == "error" for issue in project.plan.issues
            )
            missing_species = sum(
                not item.species_revision_id for item in project.plan.objects
            )
            basis = request.regulatory_basis
            regulatory_reasons: list[ReleaseCheck] = []
            network_issues = release_network_issues(project, geometry=evidence)
            if network_issues:
                regulatory_reasons.append(
                    {"code": "network_checks", "count": len(network_issues)}
                )
            if basis is None:
                regulatory_reasons.append({"code": "basis_missing"})
            else:
                if basis.pp616_status == "pending":
                    regulatory_reasons.append({"code": "pp616_pending"})
                elif not basis.pp616_reference.strip():
                    regulatory_reasons.append({"code": "pp616_reference"})
                if basis.pp1160_status == "pending":
                    regulatory_reasons.append({"code": "pp1160_pending"})
                elif not basis.pp1160_reference.strip():
                    regulatory_reasons.append({"code": "pp1160_reference"})
                if not basis.confirmed_by.strip():
                    regulatory_reasons.append({"code": "reviewer_missing"})
            if error_count or missing_species or regulatory_reasons:
                reasons: list[ReleaseCheck] = []
                if error_count:
                    reasons.append({"code": "hard_errors", "count": error_count})
                if missing_species:
                    reasons.append(
                        {"code": "species_missing", "count": missing_species}
                    )
                reasons.extend(regulatory_reasons)
                raise DomainFailure(
                    FailureReason.RELEASE_FINAL_UNAVAILABLE, {"release_checks": reasons}
                )
            if self.validation is None or not self.validation.is_current(
                project, project.plan
            ):
                raise DomainFailure(FailureReason.PLAN_VALIDATION_STALE)
        release_id = release_identity(project, request)
        existing = self.repository.get_release(project.id, release_id)
        if existing is not None:
            return ReleasePackage.model_validate_json(existing)
        source_content = self.repository.get_source(project.id)
        if source_content is None:
            raise DomainFailure(FailureReason.DXF_RELEASE_SOURCE_MISSING)
        cad = None
        if project.import_status.mode == ImportMode.AUTOCAD_LIVE:
            cad = self._cad(project)
            source_components, dxf_content = {}, b""
        else:
            source_components = self._source_components(project, source_content)
            _legacy_artifact, dxf_content = self.writer.create(
                project, source_content, source_components
            )
        scene = self.scene(project, request.scene_horizon)
        package, artifacts = build_release(
            project,
            request,
            release_id,
            dxf_content,
            scene,
            source_content,
            source_components,
            geometry=evidence,
            cad=cad,
        )
        self.repository.publish_release(
            project, release_id, package.model_dump_json(), artifacts
        )
        return package

    def get_release(self, project_id: str, release_id: str) -> ReleasePackage:
        self.repository.get(project_id, lightweight=True)
        payload = self.repository.get_release(project_id, release_id)
        if payload is None:
            raise ResourceNotFound(FailureReason.RELEASE_MISSING)
        return ReleasePackage.model_validate_json(payload)

    def download_release_artifact(
        self, project_id: str, release_id: str, artifact_id: str
    ) -> tuple[ReleaseArtifact, bytes]:
        package = self.get_release(project_id, release_id)
        artifact = next(
            (item for item in package.artifacts if item.id == artifact_id), None
        )
        if artifact is None:
            raise ResourceNotFound(FailureReason.RELEASE_ARTIFACT_MISSING)
        content = self.repository.get_export(project_id, artifact_id)
        if content is None:
            raise ResourceNotFound(FailureReason.RELEASE_ARTIFACT_MISSING)
        if (
            len(content) != artifact.size
            or sha256(content).hexdigest() != artifact.sha256
        ):
            raise DomainFailure(FailureReason.RELEASE_ARTIFACT_CORRUPT)
        return artifact, content

    def download_export(self, project_id: str, artifact_id: str) -> bytes:
        self.repository.get(project_id)
        content = self.repository.get_export(project_id, artifact_id)
        if content is None:
            raise ResourceNotFound(FailureReason.EXPORT_ARTIFACT_MISSING)
        return content

    def download_source(self, project_id: str) -> bytes:
        """Возвращать точные сохранённые байты исходника без построения карты или плана.

        Даже при неподдерживаемом CAD-содержимом или остановке расчёта оператор может
        получить исходный DXF без изменения; это отдельный сценарий от выпуска
        посадок.
        """
        project = self.repository.get(project_id, lightweight=True)
        content = self.repository.get_source(project.id)
        if content is None or project.source_file is None:
            raise DomainFailure(FailureReason.DXF_DOWNLOAD_SOURCE_MISSING)
        return content
