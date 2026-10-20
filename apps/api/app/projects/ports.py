"""Порты чтения, каталога и сохранения проекта с исходниками, квитанциями изменений и
артефактами выпуска.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from shapely.geometry.base import BaseGeometry

from app.dxf_import.contracts import ImportStatus
from app.geometry.contracts import GeometrySnapshot
from app.native_query.live.live_client import LiveSession
from app.projects.contracts import Project
from app.projects.source_contracts import SourceContentInfo


class ProjectReader(Protocol):
    """Чтение действующего снимка проекта без доступа к записи."""

    def get(self, project_id: str, *, lightweight: bool = False) -> Project: ...


@runtime_checkable
class ProjectPlacementReader(Protocol):
    """Проверенная проекция native-посадки без загрузки координат всей карты."""

    def get_placement_projection(
        self, project_id: str, *, expected_version: int
    ) -> Project | None: ...


@runtime_checkable
class ProjectGeometryKindReader(Protocol):
    """Проверенное наличие класса без загрузки координат всей карты."""

    def has_geometry_kind(
        self,
        project_id: str,
        *,
        expected_version: int,
        kind: str,
        require_geometry: bool = False,
    ) -> bool | None: ...


@dataclass(frozen=True, slots=True)
class ProjectGeometryView:
    """Только отображаемая геометрия и её метаданные; не состояние для сохранения."""

    id: str
    geometry_version: int
    import_status: ImportStatus
    geometry: GeometrySnapshot | None
    source_geometry: GeometrySnapshot | None
    site_area_m2: float | None
    planning_area_m2: float | None
    allowed_area_m2: float | None


class ProjectGeometryReader(ProjectReader, Protocol):
    def get_geometry_view(self, project_id: str) -> ProjectGeometryView: ...
    def read_display_counts(
        self,
        project_id: str,
        *,
        expected_version: int,
        geometry_field: Literal["source_geometry", "geometry"],
        kinds: set[str],
    ) -> dict[str, int]: ...


GeometryResult = TypeVar("GeometryResult")
GeometryResult_co = TypeVar("GeometryResult_co", covariant=True)


class GeometryFeatureConsumer(Protocol[GeometryResult_co]):
    """Временное производное состояние одного проверяемого чтения геометрии."""

    def accept(self, feature: Any) -> None: ...
    def finish(self) -> GeometryResult_co: ...


@runtime_checkable
class ProjectGeometryStreamReader(Protocol):
    """Передать объекты по одному, завершив проверку до возврата результата."""

    def consume_geometry_view(
        self,
        project_id: str,
        factory: Callable[
            [ProjectGeometryView, int], GeometryFeatureConsumer[GeometryResult]
        ],
    ) -> tuple[ProjectGeometryView, GeometryResult]: ...


class DisplayGeometryResource(Protocol):
    """Открытый проверенный производный ресурс, действующий только в своём context."""

    identity: str
    count: int
    coordinates: int

    def query_indexes(self, extent: tuple[float, float, float, float]) -> list[int]: ...
    def metadata(self, index: int) -> dict: ...
    def iter_metadata(self, indexes: Iterable[int]) -> Iterator[tuple[int, dict]]: ...
    def envelope(self, index: int) -> dict: ...
    def feature(self, index: int) -> dict: ...
    def geometry(self, index: int) -> BaseGeometry: ...
    def iter_geometries(
        self, indexes: Iterable[int]
    ) -> Iterator[tuple[int, BaseGeometry]]: ...
    def iter_indexes(self) -> Iterator[int]: ...
    def iter_kind_indexes(
        self,
        kinds: set[str],
        extent: tuple[float, float, float, float] | None = None,
    ) -> Iterator[int]: ...
    def iter_scene_candidates(
        self, tree_layers: set[str], curb_layers: set[str]
    ) -> Iterator[dict]: ...


@runtime_checkable
class ProjectDisplayGeometryReader(Protocol):
    """Выборочное отображение без полной коллекции и второго GEOS-индекса."""

    def open_display_geometry(
        self, project_id: str, *, wait_check: Callable[[], None] | None = None
    ) -> AbstractContextManager[
        tuple[ProjectGeometryView, DisplayGeometryResource] | None
    ]: ...


class ProjectSnapshotRepository(ProjectReader, Protocol):
    def save(
        self,
        project: Project,
        *,
        source: bytes | bytearray | None = None,
        source_components: dict[str, bytes] | None = None,
    ) -> Project: ...
    def save_retained_source(self, project: Project, root: Path) -> Project: ...
    def save_preparation_recipe(self, project: Project) -> Project: ...


class ProjectCatalogRepository(ProjectGeometryReader, Protocol):
    def create(self, project: Project) -> Project: ...
    def list(self, *, lightweight: bool = False) -> list[Project]: ...
    def delete(
        self, project_id: str, *, expected_version: int | None = None
    ) -> None: ...


class ProjectMappingRepository(ProjectReader, Protocol):
    """Выборка границы и запись первичных назначений без замены исходного графа."""

    def read_source_features(
        self,
        project_id: str,
        layer_names: set[str],
        *,
        expected_version: int,
        geometry_field: Literal["source_geometry", "geometry"] = "source_geometry",
    ) -> list[dict[str, Any]]: ...

    def save_import_mappings(
        self, project: Project, *, source_editor: bool = False
    ) -> Project: ...


class ProjectNativeSessionRepository(Protocol):
    """Привязка проверенного CAD-сеанса без замены сохранённого графа проекта."""

    def bind_native_session(
        self, project: Project, session: LiveSession
    ) -> Project: ...


class ProjectRepository(
    ProjectSnapshotRepository,
    ProjectCatalogRepository,
    ProjectMappingRepository,
    ProjectNativeSessionRepository,
    ProjectGeometryReader,
    Protocol,
):
    def save_with_receipt(
        self, project: Project, kind: str, mutation_id: str, receipt: dict
    ) -> Project: ...
    def mutation_receipt(
        self, project_id: str, kind: str, mutation_id: str
    ) -> dict | None: ...
    def delete(
        self, project_id: str, *, expected_version: int | None = None
    ) -> None: ...
    def save_source(self, project_id: str, content: bytes | bytearray) -> None: ...
    def get_source(self, project_id: str) -> bytes | None: ...
    def get_source_components(self, project_id: str) -> dict[str, bytes]: ...
    def get_source_info(
        self, project_id: str, *, prefix_bytes: int
    ) -> SourceContentInfo | None: ...
    def save_export(
        self, project_id: str, artifact_id: str, content: bytes
    ) -> None: ...
    def publish_export(
        self, project: Project, artifact_id: str, content: bytes
    ) -> Project: ...
    def get_export(self, project_id: str, artifact_id: str) -> bytes | None: ...
    def publish_release(
        self,
        project: Project,
        release_id: str,
        payload: str,
        artifacts: dict[str, bytes],
    ) -> Project: ...
    def get_release(self, project_id: str, release_id: str) -> str | None: ...
