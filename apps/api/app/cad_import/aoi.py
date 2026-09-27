"""Prepare an immutable preview workpackage without uploading the full CAD."""

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from app.cad_import.aoi_contracts import AoiManifest, AoiRequest, PreparedAoi
from app.cad_import.aoi_evidence import log_tail, retain_aoi_failure
from app.cad_import.aoi_policy import AoiPolicy
from app.cad_import.contracts import CadConversionError
from app.cad_import.process import run_converter
from app.shared.python_worker import worker_command


def prepare_aoi(
    request: AoiRequest,
    output_root: Path,
    policy: AoiPolicy | None = None,
) -> PreparedAoi:
    policy = policy or AoiPolicy()
    output_root = output_root.resolve()
    for source in (request.source, request.boundary_source):
        for path in (source.original_path, source.converted_path):
            if output_root.is_relative_to(path.resolve().parent):
                raise CadConversionError(
                    "Рабочий пакет должен находиться вне папок исходного CAD"
                )
            if path.stat().st_size > policy.max_converted_source_bytes:
                raise CadConversionError(
                    "Исходный CAD превышает бюджет подготовки рабочей территории"
                )
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aoi-", dir=output_root) as work_name:
        work = Path(work_name)
        config, drawing, result, log = (
            work / "request.json",
            work / "drawing.dxf",
            work / "manifest.json",
            work / "worker.log",
        )
        config.write_text(request.model_dump_json(), encoding="utf-8")
        policy_file = work / "policy.json"
        policy_file.write_text(json.dumps(asdict(policy)), encoding="utf-8")
        try:
            run = run_converter(
                worker_command(
                    "app.cad_import.aoi_worker",
                    str(config),
                    str(drawing),
                    str(result),
                    str(policy_file),
                ),
                drawing,
                log,
                policy,
                environment={
                    **os.environ,
                    "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                    "PYTHONUTF8": "1",
                },
            )
            if run.exit_code != 0 or not result.is_file():
                raise CadConversionError(
                    "Не удалось подготовить рабочую территорию: " + log_tail(log, 2000)
                )
        except CadConversionError as error:
            evidence = retain_aoi_failure(output_root, request, log, policy, str(error))
            raise CadConversionError(f"{error}\nДиагностика: {evidence}") from error
        manifest = AoiManifest.model_validate_json(result.read_text(encoding="utf-8"))
        manifest.diagnostics.extend(log_tail(log, policy.max_log_bytes).splitlines())
        result.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
        manifest_hash = hashlib.sha256(result.read_bytes()).hexdigest()
        destination = output_root / manifest.output_sha256
        destination.mkdir(exist_ok=True)
        drawing_path, manifest_path = (
            destination / "drawing.dxf",
            destination / f"{manifest_hash}.json",
        )
        drawing.replace(drawing_path)
        result.replace(manifest_path)
        return PreparedAoi(
            drawing_path=drawing_path,
            manifest_path=manifest_path,
            manifest=manifest,
            elapsed_seconds=run.elapsed_seconds,
            peak_memory_bytes=run.peak_memory_bytes,
        )
