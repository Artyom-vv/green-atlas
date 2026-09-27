from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceIdentity(BaseModel):
    handle: str = Field(pattern=r"^[0-9A-F]+$")
    instance_chain: list[str] = Field(default_factory=list)


class LiveDocumentCapture(BaseModel):
    """Disk identity is context, not proof that unsaved geometry matches it."""

    mode: Literal["live_document"] = "live_document"
    original_path: str
    original_disk_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    database_modified_flags: int = Field(ge=0)


class LiveCadReference(BaseModel):
    """A reference traversed in memory, not a verified/archived package file."""

    id: str = Field(pattern=r"^xref/[0-9A-F]+$")
    record_handle: str = Field(pattern=r"^[0-9A-F]+$")
    block_name: str = Field(min_length=1)
    stored_path: str = Field(min_length=1)


class SnapshotSource(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    saved: Literal[True]
    units_code: int = Field(ge=0)
    document_revision: str = Field(min_length=1)
    live_capture: LiveDocumentCapture | None = None


class CadSnapshotDependency(BaseModel):
    """A package-local file whose exact bytes contributed native geometry."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^xref/[0-9A-F]+$")
    kind: Literal["xref"]
    path: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0)
    record_handle: str = Field(pattern=r"^[0-9A-F]+$")
    block_name: str = Field(min_length=1)
    stored_path: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def require_package_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            path.is_absolute()
            or PureWindowsPath(value).drive
            or ".." in path.parts
            or "\x00" in value
        ):
            raise ValueError("dependency path must stay inside the CAD package")
        return path.as_posix()


class ExtractionEvidence(BaseModel):
    autocad_version: str = Field(min_length=1)
    plugin_version: str = Field(min_length=1)
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"]
    projection: Literal["wcs-xy-planar"]
    requested_tolerance_m: float = Field(gt=0, allow_inf_nan=False)


class UnresolvedCadReference(BaseModel):
    block_name: str = Field(min_length=1)
    stored_path: str = Field(min_length=1)


class CoverageRecord(BaseModel):
    identity: SourceIdentity
    entity_type: str = Field(min_length=1)
    layer: str
    status: Literal["native", "converted", "context", "unresolved"]
    method: str = Field(min_length=1)
    reason: str | None = None
    geometry_ids: list[str] = Field(default_factory=list)
    dependency_ids: list[str] | None = None
    unresolved_reference: UnresolvedCadReference | None = None

    @model_validator(mode="after")
    def require_explicit_resolution(self) -> CoverageRecord:
        if self.unresolved_reference is not None and self.status != "unresolved":
            raise ValueError("unresolved reference cannot authorize geometry")
        if self.status in {"native", "converted"} and not self.geometry_ids:
            raise ValueError("resolved coverage must reference geometry")
        if self.status in {"context", "unresolved"} and not self.reason:
            raise ValueError("non-calculation coverage must state a reason")
        if self.status in {"context", "unresolved"} and self.geometry_ids:
            raise ValueError("non-calculation coverage cannot authorize geometry")
        return self


class RegionLoop(BaseModel):
    role: Literal["outer", "hole"]
    closed: Literal[True]
    coordinates: list[tuple[float, float, float]] = Field(min_length=4)

    @model_validator(mode="after")
    def require_actual_closure(self) -> RegionLoop:
        if self.coordinates[0] != self.coordinates[-1]:
            raise ValueError("closed loop endpoints differ")
        return self


class RegionGeometry(BaseModel):
    id: str = Field(min_length=1)
    identity: SourceIdentity
    kind: Literal["region"]
    loops: list[RegionLoop] = Field(min_length=1)
    native_area_units2: float = Field(ge=0, allow_inf_nan=False)
    native_perimeter_units: float = Field(ge=0, allow_inf_nan=False)
    achieved_tolerance_m: float = Field(ge=0, allow_inf_nan=False)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # None for an authored REGION/HATCH. A generated AutoCAD region preserves
    # every original curve identity while those paths remain independently visible.
    derived_from: list[SourceIdentity] | None = None

    @model_validator(mode="after")
    def require_outer_loop(self) -> RegionGeometry:
        if not any(loop.role == "outer" for loop in self.loops):
            raise ValueError("region must contain an outer loop")
        if self.derived_from is not None:
            members = [
                (item.handle, tuple(item.instance_chain)) for item in self.derived_from
            ]
            if len(members) < 2 or len(members) != len(set(members)):
                raise ValueError("derived region needs distinct source curves")
            anchor = (self.identity.handle, tuple(self.identity.instance_chain))
            if anchor not in members or any(chain != anchor[1] for _, chain in members):
                raise ValueError(
                    "derived region members must share the anchor instance"
                )
        return self


class NativeAreaProposal(BaseModel):
    """A measured region from a temporary native clone, never an active obstacle.

    The immutable capture contains evidence only.  Accept/reject is a separate
    project decision tied to this source and proposal hash, not a CAD mutation.
    """

    id: str = Field(min_length=1)
    source: SourceIdentity
    source_path_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    layer: str = Field(min_length=1)
    method: Literal["explicit-chord-closure"]
    closure_gap_wcs_xy_units: float = Field(gt=0, allow_inf_nan=False)
    preview: RegionGeometry
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_separate_preview(self) -> NativeAreaProposal:
        path = "/".join([*self.source.instance_chain, self.source.handle])
        if self.id != f"area-proposal/{path}":
            raise ValueError("area proposal id must encode its source instance")
        if self.preview.identity != self.source or self.preview.derived_from is not None:
            raise ValueError("area proposal preview must belong to its one source path")
        if self.preview.id != f"{self.id}/region":
            raise ValueError("area proposal preview has an unrelated id")
        return self


class PathGeometry(BaseModel):
    """A finite native AutoCAD curve sampled in WCS.

    Closed paths stay distinct from REGION/HATCH topology: they are authored
    boundaries and may become areas only after the operator assigns a physical
    layer role.  The bridge, not the web service, owns curve evaluation and
    nested instance transforms.
    """

    id: str = Field(min_length=1)
    identity: SourceIdentity
    kind: Literal["path"]
    closed: bool
    coordinates: list[tuple[float, float, float]] = Field(min_length=2)
    achieved_tolerance_m: float = Field(ge=0, allow_inf_nan=False)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_consistent_closure(self) -> PathGeometry:
        if self.closed:
            if len(self.coordinates) < 4:
                raise ValueError("closed path must contain at least four coordinates")
            if self.coordinates[0] != self.coordinates[-1]:
                raise ValueError("closed path endpoints differ")
        elif self.coordinates[0] == self.coordinates[-1]:
            raise ValueError("open path endpoints must differ")
        return self


class PointGeometry(BaseModel):
    """A native AutoCAD point transformed into WCS."""

    id: str = Field(min_length=1)
    identity: SourceIdentity
    kind: Literal["point"]
    coordinates: tuple[float, float, float]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


CadGeometry = Annotated[
    RegionGeometry | PathGeometry | PointGeometry,
    Field(discriminator="kind"),
]


class SnapshotSummary(BaseModel):
    source_instances: int = Field(ge=0)
    native: int = Field(ge=0)
    converted: int = Field(ge=0)
    context: int = Field(ge=0)
    unresolved: int = Field(ge=0)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    complete: Literal[True]


class CadSnapshotProvenance(BaseModel):
    schema_: Literal["green-atlas.autocad-snapshot/1"] = Field(alias="schema")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    autocad_version: str = Field(min_length=1)
    plugin_version: str = Field(min_length=1)
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"]
    source_instances: int = Field(ge=0)
    native_geometry: int = Field(ge=0)
    unresolved_instances: int = Field(ge=0)
    dependencies: list[CadSnapshotDependency] = Field(default_factory=list)
    live_capture: LiveDocumentCapture | None = None
    live_references: list[LiveCadReference] | None = None

    model_config = {"populate_by_name": True}


class CadSnapshot(BaseModel):
    schema_: Literal["green-atlas.autocad-snapshot/1"] = Field(alias="schema")
    source: SnapshotSource
    extraction: ExtractionEvidence
    dependencies: list[CadSnapshotDependency] | None = None
    live_references: list[LiveCadReference] | None = None
    coverage: list[CoverageRecord]
    geometry: list[CadGeometry]
    # Absent in older captures; None preserves their signed payload exactly.
    area_proposals: list[NativeAreaProposal] | None = None
    summary: SnapshotSummary

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_coverage_ledger(self) -> CadSnapshot:
        def identity_key(identity: SourceIdentity) -> tuple[str, tuple[str, ...]]:
            return identity.handle, tuple(identity.instance_chain)

        coverage_keys = [identity_key(item.identity) for item in self.coverage]
        if len(coverage_keys) != len(set(coverage_keys)):
            raise ValueError("duplicate source instance in coverage")
        geometry_by_id = {item.id: item for item in self.geometry}
        if len(geometry_by_id) != len(self.geometry):
            raise ValueError("duplicate geometry id")
        referenced: list[str] = []
        coverage_by_key = {identity_key(item.identity): item for item in self.coverage}
        dependencies_by_id = {item.id: item for item in (self.dependencies or [])}
        if self.live_references:
            if self.source.live_capture is None or self.dependencies:
                raise ValueError("live references require a live document source")
            dependencies_by_id = {item.id: item for item in self.live_references}
            if len(dependencies_by_id) != len(self.live_references):
                raise ValueError("duplicate live CAD reference id")
        elif len(dependencies_by_id) != len(self.dependencies or []):
            raise ValueError("duplicate CAD dependency id")
        referenced_dependencies: list[str] = []
        for item in self.coverage:
            for geometry_id in item.geometry_ids:
                geometry = geometry_by_id.get(geometry_id)
                if geometry is None:
                    raise ValueError(
                        f"coverage references missing geometry: {geometry_id}"
                    )
                if identity_key(geometry.identity) != identity_key(item.identity):
                    raise ValueError(f"geometry provenance mismatch: {geometry_id}")
                referenced.append(geometry_id)
            for dependency_id in item.dependency_ids or []:
                if dependency_id not in dependencies_by_id:
                    raise ValueError(
                        f"coverage references missing dependency: {dependency_id}"
                    )
                referenced_dependencies.append(dependency_id)
        if len(referenced) != len(set(referenced)):
            raise ValueError("geometry referenced by multiple source instances")
        if set(referenced) != set(geometry_by_id):
            raise ValueError("unreferenced geometry in snapshot")
        derived_members: set[tuple[str, tuple[str, ...]]] = set()
        for geometry in self.geometry:
            if identity_key(geometry.identity) not in coverage_by_key:
                raise ValueError(f"geometry has no coverage record: {geometry.id}")
            if isinstance(geometry, RegionGeometry) and geometry.derived_from:
                anchor_coverage = coverage_by_key[identity_key(geometry.identity)]
                for member in geometry.derived_from:
                    key = identity_key(member)
                    source_coverage = coverage_by_key.get(key)
                    if (
                        source_coverage is None
                        or source_coverage.status != "native"
                        or source_coverage.layer != anchor_coverage.layer
                        or not any(
                            isinstance(geometry_by_id.get(item_id), PathGeometry)
                            for item_id in source_coverage.geometry_ids
                        )
                    ):
                        raise ValueError(
                            f"derived region source is not a native path: {key}"
                        )
                    if key in derived_members:
                        raise ValueError("source path contributes to multiple regions")
                    derived_members.add(key)
        proposal_keys: set[tuple[str, tuple[str, ...]]] = set()
        proposal_ids: set[str] = set()
        for proposal in self.area_proposals or []:
            key = identity_key(proposal.source)
            source_coverage = coverage_by_key.get(key)
            if (
                source_coverage is None
                or source_coverage.status != "native"
                or source_coverage.layer != proposal.layer
                or key in derived_members
                or key in proposal_keys
                or proposal.id in proposal_ids
            ):
                raise ValueError("area proposal lacks a unique native source path")
            paths = [
                geometry_by_id[item_id]
                for item_id in source_coverage.geometry_ids
                if isinstance(geometry_by_id.get(item_id), PathGeometry)
            ]
            if (
                len(paths) != 1
                or paths[0].closed
                or paths[0].content_sha256 != proposal.source_path_content_sha256
            ):
                raise ValueError("area proposal does not match its open source path")
            proposal_keys.add(key)
            proposal_ids.add(proposal.id)
        if set(referenced_dependencies) != set(dependencies_by_id):
            raise ValueError("unreferenced CAD dependency in snapshot")

        counts = Counter(item.status for item in self.coverage)
        expected = {
            "source_instances": len(self.coverage),
            "native": counts["native"],
            "converted": counts["converted"],
            "context": counts["context"],
            "unresolved": counts["unresolved"],
        }
        for field, value in expected.items():
            if getattr(self.summary, field) != value:
                raise ValueError(f"summary {field} does not match coverage")
        return self
