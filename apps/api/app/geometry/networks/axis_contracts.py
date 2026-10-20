"""Явная интерпретация прямой исходной оси как инженерной сети с привязкой к
свидетельствам CAD.
"""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.geometry.networks.utility_contracts import (
    UtilityContext,
    UtilityGeometryReference,
)

AXIS_COORDINATE_PRECISION_M = 0.000001


class AxisSourceReference(BaseModel):
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_id: str = Field(min_length=1, max_length=200)
    source_handle: str = Field(min_length=1, max_length=64)
    # Путь обязателен и для верхнего уровня: пропуск или догадка о цепочке
    # экземпляров не должны сослаться на другое вхождение блока.
    insert_chain: list[str]
    geometry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class UtilityAxisBinding(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    source: AxisSourceReference
    context: UtilityContext
    outside_diameter_m: float = Field(gt=0, allow_inf_nan=False)
    surface_reference: Literal["outer_surface", "protective_casing"]
    outside_size_reference: str = Field(min_length=1, max_length=1000)
    # Значений по умолчанию нет: оператор подтверждает занятую модель
    # и полные габариты. Концы трубы не выводятся из обрезки viewport
    # или произвольного фрагмента протяжённой сети.
    envelope_model: Literal["circular_sweep"]
    endpoint_model: Literal["round_full_source_extent"]
    extent_reference: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_explicit_axis(self) -> Self:
        if (
            self.context.geometry_reference != UtilityGeometryReference.AXIS
            or self.context.review_status != "confirmed"
            or self.context.installation != "underground"
            or not self.outside_size_reference.strip()
            or not self.extent_reference.strip()
        ):
            raise ValueError(
                "Подтвердите подземную ось, наружный размер с источником и полную область её охвата"
            )
        return self


class AxisDistanceEvidence(BaseModel):
    binding: UtilityAxisBinding
    axis_distance_m: float = Field(ge=0, allow_inf_nan=False)
    # Нормализация округляет WCS на эту метровую сетку. Аналитическое измерение
    # по сохранённой оси не содержит ошибки полигонального буфера.
    coordinate_precision_m: float = Field(
        default=AXIS_COORDINATE_PRECISION_M,
        ge=AXIS_COORDINATE_PRECISION_M,
        le=AXIS_COORDINATE_PRECISION_M,
    )
