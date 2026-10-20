"""Локальные лексические признаки CAD-слоёв; слабые смысловые свидетельства отделены от
физических правил.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

SEMANTIC_POLICY: Literal["native-label-observations/1"] = "native-label-observations/1"


class LabelObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        "lawn_label",
        "flowerbed_label",
        "asphalt_label",
        "concrete_label",
        "soil_label",
        "gravel_label",
        "dimension_notation",
        "nominal_designation_note",
    ]
    count: int = Field(ge=1)
    value: FiniteFloat | None = Field(default=None, gt=0, le=99999.999)
    count_notation: int | None = Field(default=None, ge=1, le=99)
    material_notation: (
        Literal[
            "steel",
            "cast_iron",
            "reinforced_concrete",
            "asbestos_cement",
            "ceramic",
            "ductile_iron",
        ]
        | None
    ) = None
    section_notation: tuple[int, int] | None = None
    unit: Literal["not_stated"] = "not_stated"
    measurement_reference: Literal["unknown"] = "unknown"
    physical_object_association: Literal["unknown"] = "unknown"
    scope: Literal["label_observation_only"] = "label_observation_only"


class LayerSemanticEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: Literal["native-label-observations/1"] = SEMANTIC_POLICY
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    complete: bool
    eligible_count: int = Field(ge=0)
    captured_count: int = Field(ge=0)
    omitted_count: int = Field(ge=0)
    default_count: int = Field(ge=0)
    observations: tuple[LabelObservation, ...] = Field(max_length=32)

    def external_features(self) -> dict[str, object]:
        """Передавать только контролируемые наблюдения без локальной идентичности
        исходника.
        """
        result = self.model_dump(
            mode="json",
            exclude={"source_sha256", "evidence_sha256", "observations"},
        )
        result.update(
            scope="label_observation_only",
            unit="not_stated",
            measurement_reference="unknown",
            physical_object_association="unknown",
            observations=[
                item.model_dump(
                    mode="json",
                    exclude_none=True,
                    exclude={
                        "unit",
                        "measurement_reference",
                        "physical_object_association",
                        "scope",
                    },
                )
                for item in self.observations
            ],
        )
        return result

    def catalog_features(self) -> dict[str, object]:
        counts: dict[str, int] = {}
        for observation in self.observations:
            counts[observation.kind] = (
                counts.get(observation.kind, 0) + observation.count
            )
        return {"complete": self.complete, "label_kinds": counts}
