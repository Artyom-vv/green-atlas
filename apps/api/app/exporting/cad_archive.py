"""Keep AutoCAD's same-capture archive for repeatable, independent releases."""

from hashlib import sha256
from pathlib import Path

from app.desktop.tickets import direct_path, read_regular
from app.native_query.capture_contracts import (
    CaptureReceipt,
    SessionTransfer,
    SessionTransferFile,
)
from app.native_query.capture_store import NativeCaptureStore
from app.native_query.live_client import LiveQueryClient
from app.native_query.process_contracts import NativeInputPackage
from app.projects.contracts import Project

MAX_CAPTURE_RECEIPT_BYTES = 4 * 1024**2


def archive_paths(client, archive: Path) -> tuple[Path, Path]:
    """Canonicalize the trusted queue, never a path supplied by the plugin."""
    queue = Path(client.queue).absolute()
    relative = archive.absolute().relative_to(queue)
    canonical_queue = queue.resolve(strict=True)
    root = direct_path(canonical_queue / relative, canonical_queue)
    return root, canonical_queue


class CadArchiveProvider:
    def __init__(self, store: NativeCaptureStore, client_factory=LiveQueryClient):
        self.store = store
        self.client_factory = client_factory

    def package(self, project: Project) -> NativeInputPackage:
        source = project.source_file
        session = source.native_session if source else None
        if session is None or source.content_sha256 != session.snapshot_sha256:
            raise ValueError("Для выпуска нужен снимок, связанный с чертежом AutoCAD")
        saved = self.store.package(project.id, session.snapshot_sha256)
        if saved is not None:
            return saved
        client = self.client_factory(pid=session.pid, timeout_seconds=600)
        root, queue = archive_paths(client, client.archive(session))
        native = root / "Native"
        receipt_path = direct_path(native / "session.json", queue)
        with read_regular(receipt_path) as stream:
            receipt_bytes = stream.read(MAX_CAPTURE_RECEIPT_BYTES + 1)
        if len(receipt_bytes) > MAX_CAPTURE_RECEIPT_BYTES:
            raise ValueError("Подтверждение CAD-архива превышает лимит")
        receipt = CaptureReceipt.model_validate_json(receipt_bytes)
        if (
            receipt.display_sha256 != session.snapshot_sha256
            or receipt.source_path != session.source_path
            or receipt.units_code != session.units_code
        ):
            raise ValueError("Архив AutoCAD относится к другому снимку")
        files = [
            SessionTransferFile(
                name="Native/session.json",
                bytes=len(receipt_bytes),
                sha256=sha256(receipt_bytes).hexdigest(),
            ),
            SessionTransferFile(
                name="Native/instances.json",
                bytes=direct_path(native / "instances.json", queue).stat().st_size,
                sha256=receipt.instances_sha256,
            ),
        ]
        files.extend(
            SessionTransferFile(
                name="Native/" + item.path,
                bytes=item.bytes,
                sha256=item.sha256,
            )
            for item in receipt.files
        )
        self.store.retain(
            project.id,
            session.snapshot_sha256,
            session.plugin_version,
            root,
            queue,
            SessionTransfer(entry="Native/session.json", files=tuple(files)),
            lambda: None,
        )
        package = self.store.package(project.id, session.snapshot_sha256)
        assert package is not None
        return package
