"""Local revision-bound location drafts; only explicit apply replaces a frame."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from shapely.geometry import MultiPolygon, Point, Polygon, box, shape

from app.location.alignment import (
    EARTH_RADIUS_M,
    automatic_pairs,
    fit_controls,
    transformed_buildings,
)
from app.location.cad import cad_review
from app.location.contracts import (
    AppliedLocation,
    LocationAlignment,
    LocationAlignmentRequest,
    LocationApplyRequest,
    LocationDraft,
    LocationResolveRequest,
    LocationState,
    LocationSuggestion,
    LocationSuggestions,
    LocationSuggestionsRequest,
    LocationTransform,
    StreetCandidate,
)
from app.native_query.capture.capture_store import NativeCaptureStore
from app.projects.contracts import Project
from app.projects.ports import ProjectReader
from app.projects.resource_scope import (
    project_resource_scope,
    register_project_resource,
)
from app.rendering.georeference import SCHEMA, render_frame
from app.rendering.location_binding import (
    applied_location_evidence,
    source_frame_binding,
)
from app.rendering.location_source import REFERENCE_SCHEMA
from app.rendering.osm import fetch_buildings, match_height, prepare_candidates
from app.scene.contracts import SceneSnapshot

MAX_LOCAL_JSON_BYTES = 24 * 1024 * 1024


class LocationError(ValueError):
    def __init__(self, message: str, code: str = "LOCATION_UNAVAILABLE") -> None:
        self.code = code
        super().__init__(message)


class LocationProviders:
    """Injectable network boundary; tests can exercise the workflow offline."""

    def configured(self) -> bool:
        from app.location.providers import load_dadata_token

        return load_dadata_token() is not None

    def suggest(self, request: LocationSuggestionsRequest) -> list[dict[str, Any]]:
        from app.location.providers import suggest_addresses

        return suggest_addresses(
            request.query, kind=request.kind, city_fias_id=request.city_fias_id
        )

    def select(self, suggestion: LocationSuggestion) -> dict[str, Any]:
        from app.location.providers import select_address

        return select_address(
            suggestion.label,
            kind="street",
            expected_fias_id=suggestion.id,
            city_fias_id=suggestion.city_fias_id,
        )

    def streets(
        self, address: dict[str, Any], cache: Path, center: Any
    ) -> dict[str, Any]:
        from app.location.providers import search_streets

        return search_streets(address, cache, near_point=center)

    def corridor(
        self, candidate: dict[str, Any], buffer_m: float, section_bbox: Any
    ) -> dict[str, Any]:
        from app.location.providers import build_street_corridor

        return build_street_corridor(
            candidate, buffer_m=buffer_m, section_bbox=section_bbox
        )

    def buildings(self, bounds: Any, cache: Path) -> dict[str, Any]:
        return fetch_buildings(tuple(bounds), cache)


def _json(value: Any) -> bytes:
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    if len(content) > MAX_LOCAL_JSON_BYTES:
        raise LocationError(
            "Участок слишком большой для привязки. Выберите меньший фрагмент улицы"
        )
    return content


def _atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.pending")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        content = stream.read(MAX_LOCAL_JSON_BYTES + 1)
    if len(content) > MAX_LOCAL_JSON_BYTES:
        raise LocationError("Локальные данные привязки превышают допустимый размер")
    result = json.loads(content)
    if not isinstance(result, dict):
        raise LocationError("Локальные данные привязки повреждены")
    return result


def _suggestion(row: dict[str, Any]) -> LocationSuggestion:
    return LocationSuggestion.model_validate(
        {key: row.get(key) for key in LocationSuggestion.model_fields if key in row}
    )


class LocationApplication:
    def __init__(
        self,
        repository: ProjectReader,
        directory: Path,
        *,
        capture_store: NativeCaptureStore | None = None,
        revision_guard: Callable[[Project], AbstractContextManager[None]] | None = None,
        providers: LocationProviders | None = None,
    ) -> None:
        self.repository = repository
        self.directory = directory
        self.capture_store = capture_store
        self.revision_guard = revision_guard
        self.providers = providers or LocationProviders()
        self._lock = RLock()
        # Network work runs outside the lock. Only the latest issued resolve may
        # publish the pointer, even when an older HTTP request finishes later.
        self._latest_resolve: dict[str, str] = {}

    def _root(self, project_id: str) -> Path:
        if (
            not project_id
            or len(project_id) > 128
            or any(
                c
                not in (
                    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                )
                for c in project_id
            )
        ):
            raise LocationError("Недопустимый идентификатор проекта")
        return self.directory / "locations" / project_id

    @staticmethod
    def _binding(project: Project) -> tuple[str, int]:
        source = project.source_file.content_sha256 if project.source_file else None
        if not source:
            raise LocationError("Для привязки нужен сохранённый исходник AutoCAD")
        return source, project.geometry_version

    def _current(self, project_id: str, source_sha: str, version: int) -> Project:
        project = self.repository.get(project_id, lightweight=True)
        if self._binding(project) != (source_sha, version):
            raise LocationError(
                "Исходник или геометрия изменились. Создайте новый черновик привязки",
                "LOCATION_STALE",
            )
        return project

    @staticmethod
    def _require_draft_frame(project: Project, draft: LocationDraft) -> None:
        if "source_frame" in draft.provenance and draft.provenance[
            "source_frame"
        ] != source_frame_binding(draft.source_sha256, project.coordinate_reference):
            raise LocationError(
                "Координатное основание изменилось — повторите привязку",
                "LOCATION_STALE",
            )

    def _store(self, project_id: str, kind: str, identifier: str, model: Any) -> None:
        with project_resource_scope(self.repository, project_id):
            register_project_resource("render-location", project_id)
            self._store_owned(project_id, kind, identifier, model)

    def _store_owned(
        self, project_id: str, kind: str, identifier: str, model: Any
    ) -> None:
        payload = model.model_dump(mode="json")
        document = {
            "project_id": project_id,
            "payload": payload,
            "sha256": sha256(_json(payload)).hexdigest(),
        }
        path = self._root(project_id) / kind / f"{identifier}.json"
        # Tokens are never reused: every resolve/align creates immutable evidence.
        if path.exists():
            raise LocationError("Идентификатор черновика уже существует")
        _atomic(path, _json(document))

    def _load(self, project_id: str, kind: str, identifier: str, model: Any) -> Any:
        if len(identifier) != 32 or any(
            c not in "0123456789abcdef" for c in identifier
        ):
            raise LocationError("Недопустимый идентификатор черновика")
        try:
            document = _read(self._root(project_id) / kind / f"{identifier}.json")
        except FileNotFoundError as error:
            raise LocationError(
                "Черновик привязки не найден. Повторите поиск", "LOCATION_DRAFT_MISSING"
            ) from error
        payload = document.get("payload")
        if (
            document.get("project_id") != project_id
            or document.get("sha256") != sha256(_json(payload)).hexdigest()
        ):
            raise LocationError("Черновик привязки повреждён")
        return model.model_validate(payload)

    def _snapshot(self, project: Project) -> SceneSnapshot:
        return SceneSnapshot(
            project_id=project.id,
            source_sha256=self._binding(project)[0],
            geometry_version=project.geometry_version,
            plan_version=1,
            horizon_year=0,
            coordinate_origin=[0, 0],
            note="location review",
            coordinate_reference=project.coordinate_reference,
        )

    def _configured(self) -> tuple[bool, str | None]:
        try:
            return self.providers.configured(), None
        except ValueError:
            return False, "Не удалось прочитать локальную настройку DaData"

    def _applied(self, project: Project) -> AppliedLocation | None:
        path = self.directory / "georeferences" / f"{project.id}.json"
        if not project.source_file or not project.source_file.content_sha256:
            return None
        try:
            data = _read(path) if path.exists() else {}
            if data and (
                data.get("project_id") != project.id
                or data.get("source_sha256") != self._binding(project)[0]
                or (
                    "location" not in data
                    and data.get("geometry_version") != project.geometry_version
                )
            ):
                return AppliedLocation(
                    status="stale",
                    source_sha256=data.get("source_sha256"),
                    geometry_version=data.get("geometry_version"),
                )
            frame = render_frame(self._snapshot(project), self.directory)
            if frame is None:
                return None
            location = data.get("location", {})
            return AppliedLocation(
                status=frame.status,
                source_sha256=frame.source_sha256,
                geometry_version=data.get("geometry_version", frame.geometry_version),
                rmse_m=frame.rmse_m,
                max_residual_m=frame.max_residual_m,
                **{
                    key: location.get(key)
                    for key in ("draft_id", "alignment_id", "label", "applied_at")
                },
            )
        except (ValueError, OSError, TypeError):
            return AppliedLocation(status="invalid")

    def get(self, project_id: str, *, prefer_applied: bool = False) -> LocationState:
        self._root(project_id)
        with self._lock:
            project = self.repository.get(project_id, lightweight=True)
            configured, message = self._configured()
            state = LocationState(
                configured=configured, applied=self._applied(project), message=message
            )
            applied = state.applied
            if (
                prefer_applied
                and applied is not None
                and applied.status not in {"stale", "invalid"}
                and applied.draft_id
                and applied.alignment_id
            ):
                try:
                    evidence = applied_location_evidence(
                        self._snapshot(project),
                        self.directory,
                        _read(self.directory / "georeferences" / f"{project.id}.json"),
                    )
                    if evidence is None:
                        raise LocationError(
                            "Применённая привязка не соответствует исходнику"
                        )
                    state.draft, state.alignment = evidence.draft, evidence.alignment
                    return state
                except (ValueError, OSError, KeyError, TypeError):
                    state.message = (
                        "Применённая привязка недоступна, повторите проверку"
                    )
            pointer = self._root(project_id) / "current.json"
            if pointer.exists():
                try:
                    current = _read(pointer)
                    draft = self._load(
                        project_id, "drafts", current["draft_id"], LocationDraft
                    )
                    if self._binding(project) == (
                        draft.source_sha256,
                        draft.geometry_version,
                    ):
                        state.draft = draft
                        if current.get("alignment_id"):
                            alignment = self._load(
                                project_id,
                                "alignments",
                                current["alignment_id"],
                                LocationAlignment,
                            )
                            if alignment.draft_id != draft.id:
                                raise LocationError(
                                    "Привязка относится к другому черновику"
                                )
                            state.alignment = alignment
                    else:
                        state.message = (
                            "Предыдущий черновик устарел после изменения исходника"
                        )
                except (ValueError, OSError, KeyError, TypeError):
                    state.message = "Предыдущий черновик недоступен. Повторите поиск"
            return state

    def suggest(
        self, project_id: str, request: LocationSuggestionsRequest
    ) -> LocationSuggestions:
        self._root(project_id)
        self.repository.get(project_id, lightweight=True)
        configured, message = self._configured()
        if not configured:
            return LocationSuggestions(
                suggestions=[],
                configured=False,
                message=message or "DaData не настроена в локальном приложении",
            )
        return LocationSuggestions(
            suggestions=[_suggestion(row) for row in self.providers.suggest(request)],
            configured=True,
        )

    def resolve(
        self, project_id: str, request: LocationResolveRequest
    ) -> LocationDraft:
        self._root(project_id)
        with self._lock:
            identifier = uuid4().hex
            self._latest_resolve[project_id] = identifier
        project = self.repository.get(project_id)
        source, version = self._binding(project)
        cad, bounds, cad_evidence = cad_review(project, self.capture_store)
        draft = LocationDraft(
            id=identifier,
            source_sha256=source,
            geometry_version=version,
            suggestion=request.suggestion,
            status="unavailable",
            message="Улица не найдена",
            cad_geometry=cad,
            cad_bounds=bounds,
            provenance={
                "cad": cad_evidence,
                "source_frame": source_frame_binding(
                    source, project.coordinate_reference
                ),
            },
        )
        try:
            selected = self.providers.select(request.suggestion)
            draft.suggestion = _suggestion(selected)
            draft.provenance["address_selection"] = {
                key: selected.get(key)
                for key in (
                    "id",
                    "label",
                    "city_fias_id",
                    "street_fias_id",
                    "street_with_type",
                    "longitude",
                    "latitude",
                    "qc_geo",
                    "provenance",
                )
            }
            draft.provenance["requested_map_center_lon_lat"] = request.center_lon_lat
            draft.provenance["requested_section_bbox"] = request.section_bbox
            # Provider revalidates the selected DaData record. Client coordinates
            # are never trusted as provider evidence; explicit map center is separate.
            result = self.providers.streets(
                selected, self.directory / "location-cache", request.center_lon_lat
            )
            candidates = result.get("candidates", [])
            if len(candidates) > 100:
                raise LocationError(
                    "Найдено слишком много улиц. Уточните участок на карте"
                )
            draft.street_candidates = [
                StreetCandidate(
                    id=row["id"], label=row["label"], geometry=row["geometry"]
                )
                for row in candidates
            ]
            draft.provenance["street_search"] = {
                key: value for key, value in result.items() if key != "candidates"
            }
            chosen = next(
                (row for row in candidates if row["id"] == request.street_candidate_id),
                None,
            )
            if request.street_candidate_id is not None and chosen is None:
                raise LocationError(
                    "Выбранный фрагмент улицы больше не доступен. Повторите выбор"
                )
            if chosen is None and len(candidates) == 1:
                chosen = candidates[0]
            if chosen is None and candidates:
                draft.status = "ambiguous_street"
                draft.message = (
                    "Найдены несколько фрагментов улицы. Выберите нужный на карте"
                )
            elif chosen is not None:
                draft.selected_street_id = chosen["id"]
                corridor = self.providers.corridor(
                    chosen, request.buffer_m, request.section_bbox
                )
                draft.search_geometry = corridor["corridor"]
                draft.provenance["corridor"] = corridor
                draft.provenance["selected_street"] = {
                    key: value for key, value in chosen.items() if key != "geometry"
                }
                buildings = self.providers.buildings(
                    corridor["bbox"], self.directory / "osm-cache"
                )
                search = shape(corridor["corridor"])
                rows = [
                    row
                    for row in buildings["features"]
                    if shape(row["geometry"]).intersects(search)
                ]
                if len(rows) > 5000:
                    raise LocationError(
                        "Слишком много зданий. Выберите меньший участок улицы"
                    )
                draft.map_buildings = {"type": "FeatureCollection", "features": rows}
                draft.provenance["buildings"] = buildings.get("provenance", {})
                draft.provenance["outside_corridor_building_count"] = len(
                    buildings["features"]
                ) - len(rows)
                draft.provenance["rejected_buildings"] = buildings.get("rejected", [])
                draft.status = "ready" if bounds is not None else "unavailable"
                draft.message = (
                    "Участок загружен. Проверьте совмещение с CAD"
                    if bounds is not None
                    else "Сначала подготовьте геометрию CAD, затем повторите поиск"
                )
        except ValueError as error:
            draft.status = (
                "no_coordinates"
                if getattr(error, "code", "") == "manual_location_required"
                else "unavailable"
            )
            draft.message = str(error)
        with self._lock:
            self._require_draft_frame(self._current(project_id, source, version), draft)
            self._store(project_id, "drafts", draft.id, draft)
            if self._latest_resolve.get(project_id) == draft.id:
                _atomic(
                    self._root(project_id) / "current.json",
                    _json({"draft_id": draft.id}),
                )
        return draft

    def _saved_alignment(
        self, project: Project, draft: LocationDraft
    ) -> LocationAlignment | None:
        """Reuse an existing bound frame only after independent polygon overlap.

        This checks whether the newly selected map area belongs on this CAD.
        It neither refits the frame nor claims fresh survey/holdout control.
        """
        try:
            saved = render_frame(self._snapshot(project), self.directory)
        except (ValueError, OSError):
            return None
        if saved is None or draft.cad_bounds is None:
            return None
        extent = saved.valid_extent_cad_xy
        if extent is None:
            # The project's declared tangent frame has no local support bound.
            # Limit this optional review to the existing CAD and its context.
            left, bottom, right, top = draft.cad_bounds
            extent = (left - 150, bottom - 150, right + 150, top + 150)
        frame = LocationTransform(
            reference_lon_lat=saved.reference_lon_lat,
            earth_radius_m=saved.earth_radius_m,
            scale=saved.scale,
            rotation=saved.rotation,
            translation=saved.translation,
            valid_extent_cad_xy=extent,
        )
        preview = transformed_buildings(draft.map_buildings, frame)
        candidates = prepare_candidates(
            [
                {
                    "id": row["id"],
                    "geometry": row["geometry"],
                    "tags": row.get("properties", {}).get("tags", {}),
                }
                for row in preview["features"]
            ]
        )
        matches: dict[str, dict[str, Any]] = {}
        support = box(*extent)
        for row in draft.cad_geometry["features"]:
            if row.get("properties", {}).get("kind") != "building":
                continue
            polygon = shape(row["geometry"])
            if not isinstance(polygon, (Polygon, MultiPolygon)) or not support.covers(
                polygon
            ):
                continue
            match = match_height(polygon, candidates)
            if match["match_status"] != "matched":
                continue
            # Duplicate CAD footprints cannot multiply one map witness.
            matches.setdefault(
                match["osm_id"],
                {
                    "cad_id": row["id"],
                    **{
                        key: match[key]
                        for key in (
                            "osm_id",
                            "footprint_coverage",
                            "osm_coverage",
                            "intersection_over_union",
                        )
                    },
                },
            )
        if len(matches) < 3:
            return None
        message = (
            "Используется сохранённая привязка. "
            f"На выбранном участке совпали здания: {len(matches)}. "
            "Проверьте наложение перед применением"
        )
        if preview["omitted_outside_extent_count"]:
            message += (
                ". За пределами области сохранённой привязки не показаны здания: "
                f"{preview['omitted_outside_extent_count']}"
            )
        return LocationAlignment(
            id=uuid4().hex,
            draft_id=draft.id,
            status="candidate",
            method="saved_frame",
            control_pairs=[],
            transform=frame,
            preview_buildings=preview,
            rmse_m=saved.rmse_m,
            max_residual_m=saved.max_residual_m,
            provenance={
                "saved_frame": saved.metadata,
                "overlap_check": {
                    "method": "existing_frame_unambiguous_parent_polygon_overlap",
                    "minimum_distinct_buildings": 3,
                    "matched_building_count": len(matches),
                    "matches": list(matches.values()),
                    "new_fit_or_holdout": False,
                },
            },
            message=message,
        )

    def _publish_alignment(
        self, project_id: str, draft: LocationDraft, alignment: LocationAlignment
    ) -> LocationAlignment:
        self._store(project_id, "alignments", alignment.id, alignment)
        pointer = self._root(project_id) / "current.json"
        current = _read(pointer) if pointer.exists() else {}
        if (
            current.get("draft_id") == draft.id
            and self._latest_resolve.get(project_id, draft.id) == draft.id
        ):
            _atomic(
                pointer,
                _json({"draft_id": draft.id, "alignment_id": alignment.id}),
            )
        return alignment

    def align(
        self, project_id: str, request: LocationAlignmentRequest
    ) -> LocationAlignment:
        with self._lock:
            draft = self._load(project_id, "drafts", request.draft_id, LocationDraft)
            project = self._current(
                project_id, draft.source_sha256, draft.geometry_version
            )
            self._require_draft_frame(project, draft)
            if draft.status != "ready" or draft.cad_bounds is None:
                raise LocationError(
                    "Сначала выберите участок улицы и подготовьте геометрию CAD"
                )
            manual = bool(request.control_pairs)
            if not manual:
                reused = self._saved_alignment(project, draft)
                if reused is not None:
                    return self._publish_alignment(project_id, draft, reused)
            mapped = draft.map_buildings
            if not manual:
                # Nearby streets often repeat house numbers. Require the selected
                # street's name before attempting automatic address correspondence.
                from app.location.providers import normalize_street_name

                expected = normalize_street_name(
                    draft.provenance.get("selected_street", {}).get(
                        "name", draft.suggestion.street or ""
                    )
                )
                mapped = {
                    "type": "FeatureCollection",
                    "features": [
                        row
                        for row in mapped["features"]
                        if expected[0]
                        and normalize_street_name(
                            row.get("properties", {})
                            .get("tags", {})
                            .get("addr:street", "")
                        )
                        == expected
                    ],
                }
            pairs = request.control_pairs or automatic_pairs(draft.cad_geometry, mapped)
            alignment = LocationAlignment(
                id=uuid4().hex,
                draft_id=draft.id,
                status="needs_controls",
                method="manual" if manual else "automatic_addresses",
                control_pairs=pairs,
                message="Не удалось определить привязку автоматически",
            )
            try:
                cad_extent = box(*draft.cad_bounds)
                map_extent = (
                    shape(draft.search_geometry).envelope
                    if draft.search_geometry
                    else None
                )
                if any(
                    not cad_extent.covers(Point(p.cad_xy))
                    or map_extent is None
                    or not map_extent.covers(Point(p.lon_lat))
                    for p in pairs
                ):
                    raise LocationError(
                        "Выберите контрольные точки внутри показанного "
                        "участка CAD и карты"
                    )
                frame, metrics = fit_controls(
                    pairs, robust=not manual, cad_bounds=draft.cad_bounds
                )
                preview = transformed_buildings(draft.map_buildings, frame)
                alignment.preview_buildings = preview
                omitted = preview["omitted_outside_extent_count"]
                if not preview["features"]:
                    raise LocationError(
                        "В проверенной области нет целых контуров зданий для "
                        "просмотра совмещения. Распределите точки шире или "
                        "выберите другой участок"
                    )
                message = (
                    "Совмещение рассчитано и проверено отдельными точками. "
                    "Проверьте наложение перед применением"
                )
                if omitted:
                    message += (
                        f". За пределами проверенной области не показаны "
                        f"здания: {omitted}"
                    )
                alignment = alignment.model_copy(
                    update={
                        "status": "candidate",
                        "transform": frame,
                        **metrics,
                        "preview_buildings": preview,
                        "message": message,
                    }
                )
            except ValueError as error:
                alignment.message = str(error)
            return self._publish_alignment(project_id, draft, alignment)

    def apply(self, project_id: str, request: LocationApplyRequest) -> LocationState:
        with project_resource_scope(self.repository, project_id):
            register_project_resource("render-georeference", project_id)
            return self._apply(project_id, request)

    def _apply(self, project_id: str, request: LocationApplyRequest) -> LocationState:
        with self._lock:
            draft = self._load(project_id, "drafts", request.draft_id, LocationDraft)
            alignment = self._load(
                project_id, "alignments", request.alignment_id, LocationAlignment
            )
            if (
                alignment.draft_id != draft.id
                or alignment.status != "candidate"
                or alignment.transform is None
            ):
                raise LocationError(
                    "Применить можно только проверенный кандидат этого черновика"
                )
            if (request.source_sha256, request.geometry_version) != (
                draft.source_sha256,
                draft.geometry_version,
            ):
                raise LocationError(
                    "Основание подтверждения не совпадает с черновиком",
                    "LOCATION_STALE",
                )
            project = self._current(
                project_id, draft.source_sha256, draft.geometry_version
            )
            self._require_draft_frame(project, draft)
            guard = (
                self.revision_guard(project)
                if self.revision_guard is not None
                else nullcontext()
            )
            with guard:
                current = self._current(
                    project_id, draft.source_sha256, draft.geometry_version
                )
                self._require_draft_frame(current, draft)
                config = {
                    "schema": SCHEMA,
                    "project_id": project_id,
                    "source_sha256": draft.source_sha256,
                    "geometry_version": draft.geometry_version,
                    "status": "candidate",
                    "earth_radius_m": EARTH_RADIUS_M,
                    **alignment.transform.model_dump(mode="json"),
                    "rmse_m": alignment.rmse_m,
                    "max_residual_m": alignment.max_residual_m,
                    "evidence_refs": [
                        f"location-draft:{draft.id}",
                        f"location-alignment:{alignment.id}",
                    ],
                    "location": {
                        "draft_id": draft.id,
                        "snapshot": {
                            "schema": REFERENCE_SCHEMA,
                            "draft_id": draft.id,
                            "sha256": sha256(
                                _json(draft.model_dump(mode="json"))
                            ).hexdigest(),
                        },
                        "alignment_id": alignment.id,
                        "label": draft.suggestion.label,
                        "applied_at": datetime.now(UTC).isoformat(),
                        "holdout_rmse_m": alignment.holdout_rmse_m,
                        "source_frame": source_frame_binding(
                            draft.source_sha256, current.coordinate_reference
                        ),
                    },
                }
                applied_location_evidence(
                    self._snapshot(current), self.directory, config
                )
                _atomic(
                    self.directory / "georeferences" / f"{project_id}.json",
                    _json(config),
                )
            return self.get(project_id)
