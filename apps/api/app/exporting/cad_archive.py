"""Чтение сохранённого архива того же захвата AutoCAD для выпуска без обращения к
открытому редактору.
"""

from pathlib import Path
from typing import Protocol

from app.desktop.tickets import direct_path
from app.native_query.capture.capture_store import NativeCaptureStore
from app.native_query.transport.process_contracts import NativeInputPackage
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason


class QueueOwner(Protocol):
    queue: Path


def archive_paths(client: QueueOwner, archive: Path) -> tuple[Path, Path]:
    """Прежняя проверка очереди для явного восстановления захвата, не для выпуска."""
    queue = Path(client.queue).absolute()
    relative = archive.absolute().relative_to(queue)
    canonical_queue = queue.resolve(strict=True)
    root = direct_path(canonical_queue / relative, canonical_queue)
    return root, canonical_queue


class CadArchiveProvider:
    def __init__(self, store: NativeCaptureStore):
        self.store = store

    def package(self, project: Project) -> NativeInputPackage:
        source = project.source_file
        session = source.native_session if source else None
        if (
            source is None
            or session is None
            or source.content_sha256 != session.snapshot_sha256
        ):
            raise DomainFailure(FailureReason.CAD_RELEASE_SOURCE_UNBOUND)
        try:
            saved = self.store.package(project.id, session.snapshot_sha256)
        except DomainFailure:
            raise
        except (OSError, ValueError) as error:
            raise DomainFailure(FailureReason.CAD_RELEASE_ARCHIVE_INVALID) from error
        if saved is None:
            raise DomainFailure(FailureReason.CAD_RELEASE_ARCHIVE_MISSING)
        return saved
