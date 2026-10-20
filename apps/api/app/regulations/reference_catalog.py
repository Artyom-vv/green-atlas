"""Домен нормативных оснований: проверяемый справочник документов и применимости.

Справочник описывает уже принятые основания; расстояния и расчётный хеш
принадлежат placement_config и не зависят от редакционного текста документов.
"""

from dataclasses import dataclass
from importlib.resources import files
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator
from typing_extensions import TypedDict

from app.shared.messages import message


@dataclass(frozen=True)
class RegulatoryRecord:
    id: str
    document_code: str
    document_title: str
    clause: str | None
    revision: str
    coverage: Literal["implemented", "partial", "unavailable"]
    machine_checkable: bool
    machine_checkable_scope: str | None
    scope: str
    source_url: str
    release_gate_machine_checkable: bool = False


class RequirementReference(BaseModel):
    document_code: str
    role: Literal["requested", "observed_replacement"]
    review_status: Literal["current_basis_unresolved", "replacement_verified"]
    source_url: str | None = None
    replaces: str | None = None
    effective_from: str | None = None
    note: str


class RequirementProfileConfig(BaseModel):
    id: str
    region: Literal["moscow"]
    current_compliance: Literal["not_established"]
    network_rule_pack: str
    references: list[RequirementReference]


class RegulatoryReleaseScope(TypedDict):
    spatial_draft: str
    pp_743: str
    pp_616: str
    pp_1160: str


class ReferenceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    registry_revision: str
    network_rule_pack: str
    network_source_url: str
    current_sp42_source_url: str
    reviewed_pp743_source_url: str
    procedural_record_ids: tuple[str, ...]
    registry_records: tuple[RegulatoryRecord, ...]
    network_record_template: RegulatoryRecord
    requirement_profile: RequirementProfileConfig
    release_scope: RegulatoryReleaseScope

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        ids = {record.id for record in self.registry_records}
        if len(ids) != len(self.registry_records):
            raise ValueError(message("config.regulations.duplicate_record"))
        if not set(self.procedural_record_ids).issubset(ids):
            raise ValueError(message("config.regulations.procedural_record_missing"))
        if (
            self.network_record_template.revision != self.network_rule_pack
            or self.requirement_profile.network_rule_pack != self.network_rule_pack
        ):
            raise ValueError(message("config.regulations.network_version_mismatch"))
        return self


REFERENCE_CATALOG = ReferenceCatalog.model_validate_json(
    files(__package__).joinpath("references.json").read_bytes()
)
