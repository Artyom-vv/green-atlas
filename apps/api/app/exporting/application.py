"""Publication use cases with atomic repository publication and stable identities."""

from collections.abc import Callable
from hashlib import sha256

from app.exporting.contracts import (
    ExportArtifact,
    ReleaseArtifact,
    ReleaseCreateRequest,
    ReleasePackage,
)
from app.exporting.ports import DxfWriterPort, ExportProjectRepository
from app.projects.contracts import Project
from app.releases.service import build_release, release_identity
from app.scene.contracts import SceneSnapshot
from app.validation.networks import release_network_issues


class ExportApplication:
    def __init__(
        self,
        *,
        repository: ExportProjectRepository,
        writer: DxfWriterPort,
        scene: Callable[[str, int], SceneSnapshot],
    ) -> None:
        self.repository = repository
        self.writer = writer
        self.scene = scene

    def _source_components(
        self, project: Project, source_content: bytes
    ) -> dict[str, bytes]:
        components = self.repository.get_source_components(project.id)
        source_file = project.source_file
        provenance = source_file.prepared_provenance if source_file else None
        if provenance is None or not provenance.drawings:
            if components:
                raise ValueError(
                    "Дополнительные DXF не связаны с проверенным комплектом"
                )
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
            raise ValueError(
                "Исходные DXF не совпадают с проверенным комплектом"
            )
        return components

    def export(self, project_id: str) -> ExportArtifact:
        project = self.repository.get(project_id)
        source_content = self.repository.get_source(project.id)
        if source_content is None:
            raise ValueError("Исходный DXF недоступен для экспорта")
        source_components = self._source_components(project, source_content)
        artifact, content = self.writer.create(
            project,
            source_content,
            source_components,
        )
        artifact.download_url = (
            f"/api/projects/{project.id}/exports/{artifact.id}/download"
        )
        # The bytes and the visible state must commit together. A sequential
        # ``save_export`` then ``save(project)`` leaves an inaccessible file
        # behind when another tab changes the project between those writes.
        self.repository.publish_export(project, artifact.id, content)
        return artifact

    def create_release(
        self, project_id: str, request: ReleaseCreateRequest
    ) -> ReleasePackage:
        project = self.repository.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if not project.plan.objects:
            raise ValueError("Добавьте хотя бы одну посадку")
        if project.source_file is None:
            raise ValueError("Исходный DXF недоступен для выпуска")
        if request.mode == "final":
            if project.source_review is not None:
                raise ValueError("Финальный выпуск недоступен: расчёт ограничений исходного комплекта ещё не выполнен. Сохранение проекта и черновой выпуск доступны.")
            error_count = sum(
                issue.severity == "error" for issue in project.plan.issues
            )
            missing_species = sum(
                not item.species_revision_id for item in project.plan.objects
            )
            basis = request.regulatory_basis
            regulatory_reasons: list[str] = []
            network_issues = release_network_issues(project)
            if network_issues:
                regulatory_reasons.append(
                    f"завершите проверки инженерных сетей: {len(network_issues)}"
                )
            if basis is None:
                regulatory_reasons.append("заполните основания ПП-616 и ПП-1160")
            else:
                if basis.pp616_status == "pending":
                    regulatory_reasons.append("определите применимость ПП-616")
                elif not basis.pp616_reference.strip():
                    regulatory_reasons.append("укажите основание решения по ПП-616")
                if basis.pp1160_status == "pending":
                    regulatory_reasons.append(
                        "определите необходимость процедуры по ПП-1160"
                    )
                elif not basis.pp1160_reference.strip():
                    regulatory_reasons.append("укажите основание решения по ПП-1160")
                if not basis.confirmed_by.strip():
                    regulatory_reasons.append("укажите ответственного за проверку")
            if error_count or missing_species or regulatory_reasons:
                reasons: list[str] = []
                if error_count:
                    reasons.append(f"устраните ошибки: {error_count}")
                if missing_species:
                    reasons.append(f"назначьте виды: {missing_species}")
                reasons.extend(regulatory_reasons)
                raise ValueError("Финальный выпуск недоступен: " + "; ".join(reasons))
        release_id = release_identity(project, request)
        existing = self.repository.get_release(project.id, release_id)
        if existing is not None:
            return ReleasePackage.model_validate_json(existing)
        source_content = self.repository.get_source(project.id)
        if source_content is None:
            raise ValueError("Исходный DXF недоступен для выпуска")
        source_components = self._source_components(project, source_content)
        _legacy_artifact, dxf_content = self.writer.create(
            project, source_content, source_components
        )
        scene = self.scene(project.id, request.scene_horizon)
        package, artifacts = build_release(
            project,
            request,
            release_id,
            dxf_content,
            scene,
            source_content,
            source_components,
        )
        self.repository.publish_release(
            project, release_id, package.model_dump_json(), artifacts
        )
        return package

    def get_release(self, project_id: str, release_id: str) -> ReleasePackage:
        self.repository.get(project_id, lightweight=True)
        payload = self.repository.get_release(project_id, release_id)
        if payload is None:
            raise KeyError("Выпуск не найден")
        return ReleasePackage.model_validate_json(payload)

    def download_release_artifact(
        self, project_id: str, release_id: str, artifact_id: str
    ) -> tuple[ReleaseArtifact, bytes]:
        package = self.get_release(project_id, release_id)
        artifact = next(
            (item for item in package.artifacts if item.id == artifact_id), None
        )
        if artifact is None:
            raise KeyError("Файл выпуска не найден")
        content = self.repository.get_export(project_id, artifact_id)
        if content is None:
            raise KeyError("Файл выпуска не найден")
        return artifact, content

    def download_export(self, project_id: str, artifact_id: str) -> bytes:
        self.repository.get(project_id)
        content = self.repository.get_export(project_id, artifact_id)
        if content is None:
            raise KeyError("Файл экспорта не найден")
        return content

    def download_source(self, project_id: str) -> bytes:
        """Return the exact uploaded bytes without deriving a map or plan.

        This is intentionally separate from a planting export. When an input
        drawing contains unsupported CAD content or a calculation is stopped
        for safety, the operator must still be able to retrieve the original
        DXF exactly as it entered the project.
        """
        project = self.repository.get(project_id, lightweight=True)
        content = self.repository.get_source(project.id)
        if content is None or project.source_file is None:
            raise ValueError("Исходный DXF недоступен для скачивания")
        return content
