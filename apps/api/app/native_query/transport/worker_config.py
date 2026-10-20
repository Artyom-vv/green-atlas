"""Общая настройка Core-задания с платформенными установкой и работником.

Windows исполняется собственным Job Object supervisor; реальная CAD-приёмка
остаётся отдельной от обнаружения файлов.
"""

import os
from pathlib import Path

from app.native_query.transport.autocad_installation import (
    desktop_platform,
    resolve_autocad_installation,
)
from app.native_query.transport.process_contracts import NativeQueryProcessConfig
from app.native_query.transport.worker_layout import resolve_worker_layout
from app.shared.failures import DomainFailure, FailureReason


def worker_config(
    database_path: str, *, job_suffix: str = ".cad-release-jobs"
) -> NativeQueryProcessConfig:
    platform = desktop_platform()
    try:
        worker = resolve_worker_layout(platform=platform)
    except (OSError, ValueError, KeyError, TypeError) as error:
        configured = os.environ.get("GREEN_ATLAS_CAD_RELEASE_WORKER")
        root = Path(configured) if configured else (
            Path.home() / "Library/Application Support/Autodesk/ApplicationAddins"
            / "GreenAtlasBridge.bundle/Contents/Workers/GreenAtlasQuery.bundle"
        )
        reason = (
            FailureReason.CAD_RELEASE_MODULE_MISSING
            if not configured and (platform == "windows" or not root.exists())
            else FailureReason.CAD_RELEASE_WORKER_DAMAGED
        )
        raise DomainFailure(reason) from error
    if platform == "macos":
        core, template = _autocad_installation()
    else:
        try:
            installation = resolve_autocad_installation(platform="windows")
        except (OSError, ValueError) as error:
            raise DomainFailure(FailureReason.CAD_RELEASE_CORE_UNAVAILABLE) from error
        core, template = installation.core_executable, installation.bootstrap_template
    return NativeQueryProcessConfig(
        core_executable=core,
        worker_bundle=worker.root,
        job_root=Path(database_path + job_suffix).absolute(),
        worker_binary_sha256=worker.binary_sha256,
        plugin_version=worker.version,
        bootstrap_template=template,
        architecture="native",
        timeout_seconds=600,
        face_protocols=worker.face_protocols,
        topology_shapes=worker.topology_shapes,
    )


def _autocad_installation() -> tuple[Path, Path]:
    """Сохраняет совместимый вход исторического CAD-writer с теми же Mac путями."""
    try:
        installation = resolve_autocad_installation(platform="macos")
    except (OSError, ValueError) as error:
        raise DomainFailure(FailureReason.CAD_RELEASE_CORE_UNAVAILABLE) from error
    return installation.core_executable, installation.bootstrap_template
