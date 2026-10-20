"""Qualify one explicitly applied map frame against its immutable CAD source.

Working areas and derived layer roles change the project's geometry revision,
but do not move the captured CAD WCS. Accepted evidence keeps its original
revision; reuse never rewrites that evidence or performs another provider call.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.geometry.contracts import CoordinateReference
from app.location.contracts import LocationAlignment, LocationDraft
from app.scene.contracts import SceneSnapshot

SOURCE_FRAME_SCHEMA = "green-atlas.location-source-frame/1"
REFERENCE_SCHEMA = "green-atlas.location-draft-reference/1"
MAX_EVIDENCE_BYTES = 24 * 1024 * 1024
_SOURCE_ERROR = (
    "Сохранённый снимок выбранной улицы повреждён, недоступен или устарел. "
    "Повторите выбор местоположения и примените привязку"
)


class RenderLocationSourceError(ValueError):
    """Applied evidence must not be replaced by another provider request."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def source_frame_binding(
    source_sha256: str, coordinate_reference: CoordinateReference
) -> dict[str, str]:
    return {
        "schema": SOURCE_FRAME_SCHEMA,
        "source_sha256": source_sha256,
        "coordinate_space": "cad_wcs_metres",
        "coordinate_reference_sha256": sha256(
            canonical_json(
                coordinate_reference.model_dump(
                    mode="json",
                    include={
                        "status",
                        "source",
                        "crs_id",
                        "axis_order",
                        "origin_wgs84",
                        "local_projection",
                        "earth_radius_m",
                    },
                )
            )
        ).hexdigest(),
    }


def _document(
    directory: Path, project_id: str, kind: str, identifier: Any
) -> dict[str, Any]:
    if (
        not isinstance(identifier, str)
        or re.fullmatch(r"[a-f0-9]{32}", identifier) is None
    ):
        raise ValueError("invalid accepted evidence identifier")
    path = directory / "locations" / project_id / kind / f"{identifier}.json"
    if path.is_symlink():
        raise ValueError("accepted evidence is a symbolic link")
    with path.open("rb") as stream:
        content = stream.read(MAX_EVIDENCE_BYTES + 1)
    if len(content) > MAX_EVIDENCE_BYTES:
        raise ValueError("accepted evidence is too large")
    document = json.loads(content)
    if (
        not isinstance(document, dict)
        or document.get("project_id") != project_id
        or not isinstance(document.get("payload"), dict)
        or document["payload"].get("id") != identifier
        or document.get("sha256")
        != sha256(canonical_json(document["payload"])).hexdigest()
    ):
        raise ValueError("accepted evidence integrity mismatch")
    return document


def _legacy_local_reference(reference: CoordinateReference) -> bool:
    # Old accepted drafts state CAD WCS metres, while the captured native source
    # SHA binds its units. A newly declared/reordered geographic frame cannot be
    # inferred to be that same historical frame without an explicit fingerprint.
    return (
        reference.status in {"unknown", "local"}
        and reference.source == "none"
        and reference.axis_order in {None, "xy"}
        and reference.crs_id is None
        and reference.origin_wgs84 is None
        and reference.local_projection is None
        and reference.earth_radius_m is None
    )


@dataclass(frozen=True)
class AppliedLocationEvidence:
    draft: LocationDraft
    alignment: LocationAlignment
    original_geometry_version: int
    binding_basis: str


def applied_location_evidence(
    snapshot: SceneSnapshot, directory: Path, config: dict[str, Any]
) -> AppliedLocationEvidence | None:
    """Load the exact accepted pair; only qualified locations cross revisions."""
    if "location" not in config:
        return None
    try:
        project_id = snapshot.project_id
        if (
            not project_id
            or len(project_id) > 128
            or re.fullmatch(r"[a-zA-Z0-9_-]+", project_id) is None
            or config.get("project_id") != project_id
            or not snapshot.source_sha256
            or config.get("source_sha256") != snapshot.source_sha256
            or type(config.get("geometry_version")) is not int
        ):
            raise ValueError("applied location source binding mismatch")
        location = config["location"]
        reference = location["snapshot"]
        if (
            reference["schema"] != REFERENCE_SCHEMA
            or reference["draft_id"] != location["draft_id"]
            or not isinstance(reference["sha256"], str)
            or re.fullmatch(r"[a-f0-9]{64}", reference["sha256"]) is None
        ):
            raise ValueError("invalid accepted draft reference")
        stored_draft = _document(directory, project_id, "drafts", location["draft_id"])
        stored_alignment = _document(
            directory, project_id, "alignments", location["alignment_id"]
        )
        payload = stored_draft["payload"]
        if (
            stored_draft["sha256"] != reference["sha256"]
            or payload.get("source_sha256") != snapshot.source_sha256
            or type(payload.get("geometry_version")) is not int
            or payload["geometry_version"] != config["geometry_version"]
        ):
            raise ValueError("accepted draft source or original revision mismatch")
        draft = LocationDraft.model_validate(payload)
        alignment = LocationAlignment.model_validate(stored_alignment["payload"])
        if (
            draft.status != "ready"
            or not draft.selected_street_id
            or alignment.draft_id != draft.id
            or alignment.status != "candidate"
            or alignment.transform is None
            or config.get("status") != "candidate"
            or config.get("evidence_refs")
            != [f"location-draft:{draft.id}", f"location-alignment:{alignment.id}"]
            or location.get("label") != draft.suggestion.label
            or location.get("holdout_rmse_m") != alignment.holdout_rmse_m
        ):
            raise ValueError("applied location does not describe the accepted pair")
        for key, value in alignment.transform.model_dump(mode="json").items():
            if config.get(key) != value:
                raise ValueError(
                    "applied transform differs from the accepted alignment"
                )
        for key in ("rmse_m", "max_residual_m"):
            if config.get(key) != getattr(alignment, key):
                raise ValueError("applied metrics differ from the accepted alignment")
        if draft.provenance.get("cad", {}).get("coordinate_space") != "cad_wcs_metres":
            raise ValueError("accepted CAD coordinate frame is unavailable")
        binding = location.get("source_frame")
        if binding is not None:
            draft_binding = draft.provenance.get("source_frame")
            if binding != source_frame_binding(
                snapshot.source_sha256, snapshot.coordinate_reference
            ) or (
                draft_binding != binding
                and (
                    draft_binding is not None
                    or not _legacy_local_reference(snapshot.coordinate_reference)
                )
            ):
                raise ValueError("applied CAD coordinate frame changed")
            basis = (
                "explicit_source_frame"
                if draft_binding is not None
                else "explicit_source_frame_accepted_legacy_evidence"
            )
        else:
            if "source_frame" in draft.provenance or not _legacy_local_reference(
                snapshot.coordinate_reference
            ):
                raise ValueError("legacy CAD coordinate frame cannot be qualified")
            basis = "accepted_legacy_cad_wcs_metres"
        return AppliedLocationEvidence(
            draft, alignment, config["geometry_version"], basis
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise RenderLocationSourceError(_SOURCE_ERROR) from error
