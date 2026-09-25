"""Selected-instance measurements, not a full obstacle inventory or planting verdict."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Hex32 = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Route = Annotated[str, Field(pattern=r"^[0-9A-F]{1,16}(/[0-9A-F]{1,16}){0,63}$")]
PROTOCOL = "green-atlas.native-object-query/2"
MAX_MEASUREMENTS = 262_144
MAX_QUERY_MEMBERS = 4096


class NativeDto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NativeTarget(NativeDto):
    route: Route
    capability: Literal["area", "curve", "closed_area"]
    additional_routes: tuple[Route, ...] = Field(default=(), max_length=255)
    face_id: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def consistent_group(self) -> "NativeTarget":
        if self.face_id and (self.capability != "area" or self.additional_routes):
            raise ValueError("Derived face requires one capture catalogue target")
        if self.additional_routes:
            members = (self.route, *self.additional_routes)
            parents = {route.rpartition("/")[0] for route in members}
            if self.capability != "area" or len(set(members)) != len(members) or len(parents) != 1:
                raise ValueError("Native group requires unique curves in one instance")
        return self


class NativeObjectQuery(NativeDto):
    request_id: Hex32
    source_sha256: Sha256
    units_code: int = Field(ge=0, le=24)
    targets: tuple[NativeTarget, ...] = Field(min_length=1, max_length=4096)
    points: tuple[tuple[FiniteFloat, FiniteFloat, FiniteFloat], ...] = Field(
        min_length=1, max_length=4096
    )

    @model_validator(mode="after")
    def bounded_unique_batch(self) -> "NativeObjectQuery":
        if len(self.targets) * len(self.points) > MAX_MEASUREMENTS:
            raise ValueError("Native measurement batch exceeds limit")
        if len({(target.route, target.face_id) for target in self.targets}) != len(self.targets):
            raise ValueError("Duplicate native instance target")
        if sum(1 + len(target.additional_routes) for target in self.targets) > MAX_QUERY_MEMBERS:
            raise ValueError("Native group total member limit")
        return self


class NativePointMeasurement(NativeDto):
    point_index: int = Field(ge=0)
    status: int
    membership: Literal["occupied", "edge", "outside", "unknown"]
    distance_units: Annotated[FiniteFloat, Field(ge=0)] | None
    error: str


class NativeObjectMeasurement(NativeDto):
    route: Route
    additional_routes: tuple[Route, ...] = ()
    face_id: int = Field(default=0, ge=0)
    entity_type: str
    layer: str
    capability: Literal["area", "curve", "unavailable"]
    interior_known: bool
    preparation_error: str
    prepare_ms: Annotated[FiniteFloat, Field(ge=0)]
    answers: tuple[NativePointMeasurement, ...]

    @model_validator(mode="after")
    def consistent_capability(self) -> "NativeObjectMeasurement":
        if self.interior_known != (self.capability == "area"):
            raise ValueError("Curve/unavailable measurement cannot establish interior")
        for answer in self.answers:
            if (self.capability != "area" or answer.status != 0 or answer.error) and (
                answer.membership != "unknown"
            ):
                raise ValueError("Unmeasured membership must remain unknown")
            if self.capability == "unavailable" and answer.distance_units is not None:
                raise ValueError("Unavailable object cannot report measured distance")
        return self


class NativeObjectReply(NativeDto):
    schema_: Literal["green-atlas.native-object-query/2"] = Field(alias="schema")
    scope: Literal["selected_instance_measurements_only"]
    request_id: Hex32
    request_sha256: Sha256
    plugin_version: str = Field(min_length=1)
    source_sha256: Sha256
    database_revision: str = Field(min_length=1)
    database_modified_flags: int = Field(ge=0)
    units_code: int = Field(ge=0, le=24)
    point_count: int = Field(ge=1, le=4096)
    elapsed_ms: Annotated[FiniteFloat, Field(ge=0)]
    objects: tuple[NativeObjectMeasurement, ...] = Field(min_length=1, max_length=4096)
