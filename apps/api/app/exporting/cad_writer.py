"""Production CAD release adapter: retained capture -> AutoCAD -> separate DWG."""

import json
import os
import plistlib
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

from app.dxf_import.units import meters_per_dxf_unit
from app.exporting.cad_archive import CadArchiveProvider
from app.exporting.cad_contracts import CadPlant, CadRelease
from app.exporting.cad_process import write_cad_release
from app.native_query.capture_store import NativeCaptureStore
from app.native_query.process_contracts import NativeQueryProcessConfig
from app.projects.contracts import Project


def _autocad_installation() -> tuple[Path, Path]:
    """Locate the installed editor, not the build machine's AutoCAD path."""
    configured = os.environ.get("GREEN_ATLAS_AUTOCAD_ROOT")
    candidates = [Path(configured)] if configured else [
        Path("/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app"),
        Path("/Applications/AutoCAD 2027.app"),
        Path.home() / "Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app",
        Path.home() / "Applications/AutoCAD 2027.app",
    ]

    def valid_installation(app: Path) -> tuple[Path, Path] | None:
        try:
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        except (OSError, ValueError):
            return None
        if info.get("CFBundleIdentifier") != "com.autodesk.AutoCAD2027":
            return None
        core = app / "Contents/Helpers/AcCoreConsole.app/Contents/MacOS/AcCoreConsole"
        templates = app / "Contents/Resources/UserDataCache"
        preferred = templates / "en-us/Template/acadiso.dwt"
        template = next((path for path in (preferred, *sorted(templates.glob("*/Template/acadiso.dwt"))) if path.is_file()), None)
        if core.is_file() and os.access(core, os.X_OK) and template is not None:
            return core, template
        return None

    for app in candidates:
        found = valid_installation(app)
        if found is not None:
            return found
    if not configured and sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["/usr/bin/mdfind", "kMDItemCFBundleIdentifier == 'com.autodesk.AutoCAD2027'"],
                capture_output=True, text=True, timeout=3, check=False,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    found = valid_installation(Path(line))
                    if found is not None:
                        return found
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise ValueError(
        "Не найден совместимый AutoCAD 2027 с модулем выпуска и шаблоном acadiso.dwt. "
        "Установите полную версию AutoCAD либо укажите GREEN_ATLAS_AUTOCAD_ROOT"
    )


def release_config(database_path: str) -> NativeQueryProcessConfig:
    bundle_path = os.environ.get("GREEN_ATLAS_CAD_RELEASE_WORKER")
    if not bundle_path:
        # The packaged desktop passes an explicit worker. A local API started
        # beside an installed Mac add-in uses that same installed worker, not
        # an arbitrary build artefact or an older backup bundle.
        installed = (
            Path.home()
            / "Library/Application Support/Autodesk/ApplicationAddins"
            / "GreenAtlasBridge.bundle/Contents/Workers/GreenAtlasQuery.bundle"
        )
        if not installed.is_dir():
            raise ValueError(
                "Модуль выпуска AutoCAD не установлен. Обновите комплект Green Atlas"
            )
        bundle_path = str(installed)
    bundle = Path(bundle_path).absolute()
    if not (bundle / "Contents/Info.plist").is_file() or not (
        bundle / "Contents/MacOS/GreenAtlasBridge"
    ).is_file():
        raise ValueError("Модуль выпуска AutoCAD повреждён. Переустановите Green Atlas")
    info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
    core, template = _autocad_installation()
    return NativeQueryProcessConfig(
        core_executable=core,
        worker_bundle=bundle,
        job_root=Path(database_path + ".cad-release-jobs").absolute(),
        worker_binary_sha256=sha256(
            (bundle / "Contents/MacOS/GreenAtlasBridge").read_bytes()
        ).hexdigest(),
        plugin_version=info["CFBundleShortVersionString"],
        bootstrap_template=template,
        architecture="native",
        timeout_seconds=600,
    )


class AutoCadReleaseWriter:
    def __init__(
        self,
        database_path: str,
        archive=None,
        config_factory=release_config,
        write=write_cad_release,
    ):
        self.database_path = database_path
        self.archive = archive or CadArchiveProvider(
            NativeCaptureStore(
                Path(database_path + ".cad-archives"),
            )
        )
        self.config_factory = config_factory
        self.write = write

    def create(self, project: Project) -> CadRelease:
        if project.plan is None or not project.plan.objects:
            raise ValueError("Добавьте посадки перед выпуском CAD")
        source = project.source_file
        session = source.native_session if source else None
        if session is None:
            raise ValueError("Исходный чертёж не связан со снимком AutoCAD")
        scale = meters_per_dxf_unit(session.units_code)
        if scale is None:
            if session.units_code != 0 or not source.units_assumed:
                raise ValueError("Не удалось определить масштаб CAD-выпуска")
            scale = 1.0  # The same explicit metre assumption used on import.
        plants = tuple(
            CadPlant(
                id=item.id,
                kind=item.kind,
                x=item.x / scale,
                y=item.y / scale,
                radius=item.radius / scale,
            )
            for item in project.plan.objects
        )
        config = self.config_factory(self.database_path)
        if (
            isinstance(config, NativeQueryProcessConfig)
            and config.plugin_version != session.plugin_version
        ):
            raise ValueError(
                "Версия модуля выпуска не совпадает с версией захвата AutoCAD. "
                "Обновите плагин и откройте чертёж заново"
            )
        package = self.archive.package(project)
        result = self.write(package, plants, session.units_code, config)
        result.files["green-atlas-plan.json"] = json.dumps(
            {
                "schema": "green-atlas.cad-plan/1",
                "project_id": project.id,
                "state_version": project.state_version,
                "geometry_version": project.geometry_version,
                "snapshot_sha256": session.snapshot_sha256,
                "cad_package_sha256": package.sha256,
                "source_sha256": session.source_sha256,
                "metres_per_unit": scale,
                "plan": project.plan.model_dump(mode="json"),
                "entry": result.entry,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
        return result
