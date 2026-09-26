"""CAD release payloads. No interpretation of DWG geometry in this module."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, FiniteFloat

from app.native_query.contracts import NativeDto, Sha256


class CadPlant(NativeDto):
    id: Annotated[str, Field(pattern=r"^[0-9a-fA-F_-]{1,80}$")]
    kind: Literal["tree", "shrub"]
    x: FiniteFloat
    y: FiniteFloat
    radius: Annotated[FiniteFloat, Field(gt=0)]


class CadWrittenPlant(CadPlant):
    handle: Annotated[str, Field(pattern=r"^[0-9A-F]{1,16}$")]


class CadWriteReceipt(NativeDto):
    schema_: Literal["green-atlas.cad-release/1"] = Field(alias="schema")
    request_sha256: Sha256
    source_sha256: Sha256
    result_sha256: Sha256
    source_instances: int = Field(ge=0)
    result_instances: int = Field(ge=0)
    units_code: int = Field(ge=0, le=24)
    reopened: Literal[True]
    plantings: tuple[CadWrittenPlant, ...]


@dataclass(frozen=True)
class CadRelease:
    """Relative files to publish together; the DWG's relative XREFs need them."""

    files: dict[str, bytes]
    entry: str
    receipt: CadWriteReceipt


MAX_PLANTS = 100000
MAX_REQUEST_BYTES = 4 * 1024**2


def encode_plantings(plants: tuple[CadPlant, ...], units: int) -> bytes:
    if not 1 <= len(plants) <= MAX_PLANTS or len({p.id for p in plants}) != len(plants):
        raise ValueError("Состав посадок для CAD-выпуска некорректен")
    if not 0 <= units <= 24:
        raise ValueError("Неизвестные единицы CAD")
    rows = ["green-atlas.cad-release/1", f"{units} {len(plants)}"]
    rows.extend(
        f"{p.id} {p.kind} {p.x:.17g} {p.y:.17g} {p.radius:.17g}" for p in plants
    )
    content = ("\n".join(rows) + "\n").encode("ascii")
    if len(content) > MAX_REQUEST_BYTES:
        raise ValueError("Слишком большой состав CAD-выпуска")
    return content


def validate_receipt(
    receipt: CadWriteReceipt,
    plants: tuple[CadPlant, ...],
    units: int,
    request_sha: str,
    source_sha: str,
    result_sha: str,
) -> None:
    actual = tuple(
        CadPlant.model_validate(p.model_dump(exclude={"handle"}))
        for p in receipt.plantings
    )
    if (
        receipt.units_code != units
        or receipt.request_sha256 != request_sha
        or receipt.source_sha256 != source_sha
        or receipt.result_sha256 != result_sha
        or receipt.result_instances != receipt.source_instances + len(plants)
        or len({p.handle for p in receipt.plantings}) != len(plants)
        or actual != plants
    ):
        raise ValueError(
            "Повторное открытие CAD не подтвердило состав и координаты посадок"
        )
