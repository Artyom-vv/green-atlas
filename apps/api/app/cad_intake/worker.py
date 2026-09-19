"""Isolated package inspection; never imports HTTP, project storage or agents."""

import sys
from pathlib import Path

from pydantic import BaseModel

from app.cad_import.cache import file_sha256
from app.cad_import.conversion import LibreDwgConverter
from app.cad_import.dxf_inspection import DxfInspector
from app.cad_import.package import PackageInspector, write_package
from app.cad_import.policy import ConversionPolicy
from app.cad_intake.contracts import CadIntakeRequest


class InspectionWork(BaseModel):
    root: Path
    cache: Path
    converter: Path | None = None
    output: Path
    request: CadIntakeRequest
    policy: ConversionPolicy = ConversionPolicy()


def execute(work: InspectionWork) -> None:
    selected = [
        (work.request.entry, work.request.entry_sha256),
        *(
            (item.path, item.sha256)
            for item in work.request.additional_entries
        ),
    ]
    entries = [(work.root / path).resolve(strict=True) for path, _ in selected]
    if any(not entry.is_relative_to(work.root.resolve()) for entry in entries):
        raise ValueError("Выход за пределы исходного комплекта")
    policy = work.policy
    # Native DXF already is the parser input, not a compressed DWG awaiting
    # conversion. Use the existing DXF inspection budget, not the DWG budget.
    for entry, (_, expected_sha256) in zip(entries, selected, strict=True):
        entry_budget = (
            policy.max_output_bytes
            if entry.suffix.lower() == ".dxf"
            else policy.max_source_bytes
        )
        if entry.stat().st_size > entry_budget:
            raise ValueError("Исходный чертёж превышает бюджет чтения CAD")
        if file_sha256(entry) != expected_sha256:
            raise ValueError("Исходный чертёж изменился после выбора")
    converter_path = work.converter
    inspector = PackageInspector(
        DxfInspector(work.cache, policy),
        converter_factory=(lambda: LibreDwgConverter(converter_path, work.cache, policy))
        if converter_path is not None else None,
    )
    package = inspector.inspect_entries(
        work.root,
        [Path(path) for path, _ in selected],
        list(work.request.overrides),
    )
    # Verify every selected identity after inspection, even when a reader rejected it.
    for entry, (_, expected_sha256) in zip(entries, selected, strict=True):
        if file_sha256(entry) != expected_sha256:
            raise ValueError("Исходный чертёж изменился во время проверки")
    write_package(package, work.output)


if __name__ == "__main__":
    execute(
        InspectionWork.model_validate_json(
            Path(sys.argv[1]).read_text(encoding="utf-8")
        )
    )
