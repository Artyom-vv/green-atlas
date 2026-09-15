"""Isolated package inspection; never imports HTTP, project storage or agents."""

import sys
from pathlib import Path

from pydantic import BaseModel

from app.cad_import.cache import file_sha256
from app.cad_import.conversion import LibreDwgConverter
from app.cad_import.package import PackageInspector, write_package
from app.cad_import.policy import ConversionPolicy
from app.cad_intake.contracts import CadIntakeRequest


class InspectionWork(BaseModel):
    root: Path
    cache: Path
    converter: Path
    output: Path
    request: CadIntakeRequest


def execute(work: InspectionWork) -> None:
    entry = (work.root / work.request.entry).resolve(strict=True)
    if not entry.is_relative_to(work.root.resolve()):
        raise ValueError("Выход за пределы исходного комплекта")
    if entry.stat().st_size > ConversionPolicy().max_source_bytes:
        raise ValueError("Исходный чертёж превышает бюджет чтения CAD")
    if file_sha256(entry) != work.request.entry_sha256:
        raise ValueError("Исходный чертёж изменился после выбора")
    converter = LibreDwgConverter(work.converter, work.cache)
    inspector = PackageInspector(converter)
    package = inspector.inspect(
        work.root, Path(work.request.entry), list(work.request.overrides)
    )
    # Verify the selected identity also after inspection, even when a reader rejected it.
    if file_sha256(entry) != work.request.entry_sha256:
        raise ValueError("Исходный чертёж изменился во время проверки")
    write_package(package, work.output)


if __name__ == "__main__":
    execute(
        InspectionWork.model_validate_json(
            Path(sys.argv[1]).read_text(encoding="utf-8")
        )
    )
