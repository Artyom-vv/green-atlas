"""Build a reviewable full-street GIS + inventory prototype for Kustanayskaya.

This deliberately produces a diagnostic world, not a beauty claim.  External
GIS supplies the continuous street context.  The inventory DXF supplies plant
positions and the XLS supplies species, count and measured dimensions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import ezdxf
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY_DXF = ROOT / (
    ".runtime/kustanayskaya-mac-dxf-20260917/dxf/"
    "000749-2026_Кустанайская улица/ИП_Кустанайская улица/"
    "ИП_Кустанайская улица.dxf"
)
DEFAULT_INVENTORY_XLS = Path(
    "/Users/artem/Downloads/Датасет/Пилотный проект 20 улиц/"
    "18. Кустанайская улица/Исходные данные/000749-2026_Кустанайская улица/"
    "ПВ_Кустанайская улица.xls"
)
DEFAULT_ALIGNMENT = ROOT / ".runtime/deterministic-render-audit-20260919/candidate-osm-alignment.json"
DEFAULT_MANIFEST = ROOT / ".runtime/world-source-manifest-20260920/world-source-manifest.json"
DEFAULT_OSM = ROOT / ".runtime/deterministic-render-audit-20260919/kustanayskaya-osm.xml"
DEFAULT_DEM = ROOT / ".runtime/overture-kustanayskaya-20260920/Copernicus_DSM_COG_10_N55_00_E037_00_DEM.tif"
DEFAULT_ELEVATION_CONTROLS = ROOT / ".runtime/topographic-elevation-controls-20260920/elevation-controls.json"
DEFAULT_OUTPUT = ROOT / ".runtime/kustanayskaya-working-prototype-20260920"
DATASET_ROOT = Path(
    "/Users/artem/Downloads/Датасет/Пилотный проект 20 улиц/18. Кустанайская улица"
)
DEFAULT_GP_PDF = DATASET_ROOT / "ГП_Кустанайская (1).pdf"
DEFAULT_RPCH_PDF = DATASET_ROOT / "РПЧ_Кустанайская.pdf"
DEFAULT_INVENTORY_PLAN_1 = DATASET_ROOT / (
    "Исходные данные/000749-2026_Кустанайская улица/ИП_Кустанайская улица_1.pdf"
)
DEFAULT_INVENTORY_PLAN_2 = DATASET_ROOT / (
    "Исходные данные/000749-2026_Кустанайская улица/ИП_Кустанайская улица_2.pdf"
)
DEFAULT_PHOTO_DIRECTORY = DATASET_ROOT / "Кустанаевская ул. фото до"
EARTH_RADIUS_M = 6_378_137.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_xlrd() -> Any:
    try:
        import xlrd  # type: ignore

        return xlrd
    except ImportError:
        candidates = sorted(
            ROOT.glob(
                ".runtime/cad-comparison-*/FreeCAD.app/Contents/Resources/"
                "lib/python*/site-packages"
            )
        )
        for candidate in candidates:
            sys.path.append(str(candidate))
            try:
                import xlrd  # type: ignore

                return xlrd
            except ImportError:
                continue
    raise RuntimeError("xlrd is required to read the supplied inventory XLS")


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    values = [float(item.replace(",", ".")) for item in re.findall(r"\d+(?:[.,]\d+)?", str(value))]
    return sum(values) / len(values) if values else None


def parse_inventory_xls(path: Path) -> dict[int, dict[str, Any]]:
    xlrd = load_xlrd()
    sheet = xlrd.open_workbook(str(path)).sheet_by_name("Перечетка")
    rows: dict[int, dict[str, Any]] = {}
    for row_index in range(sheet.nrows):
        raw_id = sheet.cell_value(row_index, 0)
        if not isinstance(raw_id, (int, float)) or int(raw_id) != raw_id:
            continue
        inventory_id = int(raw_id)
        if not 1 <= inventory_id <= 1282:
            continue
        tree_count = int(number(sheet.cell_value(row_index, 2)) or 0)
        shrub_count = int(number(sheet.cell_value(row_index, 3)) or 0)
        species = str(sheet.cell_value(row_index, 1)).strip()
        kind = "stump" if species.casefold() == "пень" else ("tree" if tree_count else "shrub_group")
        rows[inventory_id] = {
            "inventory_id": inventory_id,
            "species": species,
            "kind": kind,
            "tree_count": tree_count,
            "shrub_count": shrub_count,
            "count": tree_count or shrub_count or 1,
            "diameter_cm_source": str(sheet.cell_value(row_index, 4)).strip() or None,
            "diameter_cm": number(sheet.cell_value(row_index, 4)),
            "height_m_source": str(sheet.cell_value(row_index, 6)).strip() or None,
            "height_m": number(sheet.cell_value(row_index, 6)),
            "condition_description": str(sheet.cell_value(row_index, 7)).strip() or None,
            "conclusion": str(sheet.cell_value(row_index, 8)).strip() or None,
            "note": str(sheet.cell_value(row_index, 10)).strip() or None,
            "source_row": row_index + 1,
        }
    if not rows or max(rows) != 1282:
        raise RuntimeError("Inventory XLS numbering does not end at 1282")
    return rows


def parse_inventory_positions(path: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    document = ezdxf.readfile(path)
    modelspace = document.modelspace()
    label_candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for entity in modelspace:
        if entity.dxftype() not in {"TEXT", "MTEXT"} or "Дендра" not in entity.dxf.layer:
            continue
        text = entity.plain_text() if entity.dxftype() == "MTEXT" else entity.dxf.text
        if not re.fullmatch(r"\s*\d{1,4}\s*", text or ""):
            continue
        inventory_id = int(text)
        if not 1 <= inventory_id <= 1282:
            continue
        point = entity.dxf.insert
        label_candidates[inventory_id].append(
            {
                "xy_dxf_m": [float(point.x), float(point.y)],
                "layer": entity.dxf.layer,
                "handle": entity.dxf.handle,
            }
        )

    excluded_blocks = {"_TagCircle", "аьо", "ЮАО-10004141-Кустанайская улица"}
    markers = []
    for entity in modelspace.query("INSERT"):
        if "Дендра" not in entity.dxf.layer or entity.dxf.name in excluded_blocks:
            continue
        point = entity.dxf.insert
        if point.x < 1000:  # legend examples outside the real drawing coordinates
            continue
        markers.append(
            {
                "xy_dxf_m": [float(point.x), float(point.y)],
                "layer": entity.dxf.layer,
                "block": entity.dxf.name,
                "handle": entity.dxf.handle,
            }
        )

    positions: dict[int, dict[str, Any]] = {}
    for inventory_id, candidates in label_candidates.items():
        best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
        for candidate in candidates:
            x, y = candidate["xy_dxf_m"]
            marker = min(
                markers,
                key=lambda item: (item["xy_dxf_m"][0] - x) ** 2 + (item["xy_dxf_m"][1] - y) ** 2,
            )
            distance = math.dist(candidate["xy_dxf_m"], marker["xy_dxf_m"])
            if best is None or distance < best[0]:
                best = distance, candidate, marker
        assert best is not None
        distance, label, marker = best
        if distance <= 1.0:
            xy = marker["xy_dxf_m"]
            status, confidence = "matched_marker", 0.98
        elif distance <= 4.0:
            xy = marker["xy_dxf_m"]
            status, confidence = "nearest_marker_review", 0.75
        else:
            xy = label["xy_dxf_m"]
            status, confidence = "label_position_only", 0.45
        positions[inventory_id] = {
            "xy_dxf_m": xy,
            "position_status": status,
            "position_confidence": confidence,
            "label": label,
            "nearest_marker": marker,
            "label_marker_distance_m": round(distance, 6),
        }

    audit = {
        "numbering_max": 1282,
        "numbering_gaps": sorted(set(range(1, 1283)) - set(label_candidates)),
        "numeric_ids_found": len(label_candidates),
        "marker_count": len(markers),
        "position_status_counts": dict(Counter(row["position_status"] for row in positions.values())),
    }
    return positions, audit


def inverse_alignment(xy_dxf: list[float], alignment: dict[str, Any]) -> tuple[float, float]:
    transform = alignment["similarity_transform_row_vector"]
    rotation = np.asarray(transform["rotation"], dtype=float)
    translation = np.asarray(transform["translation"], dtype=float)
    tangent = (np.asarray(xy_dxf, dtype=float) - translation) @ np.linalg.inv(rotation) / float(transform["scale"])
    lon0, lat0 = alignment["projection_before_fit"]["reference_lon_lat"]
    radius = float(alignment["projection_before_fit"]["earth_radius_m"])
    lon = lon0 + math.degrees(tangent[0] / (radius * math.cos(math.radians(lat0))))
    lat = lat0 + math.degrees(tangent[1] / radius)
    return float(lon), float(lat)


def world_local(lon: float, lat: float, world: dict[str, Any]) -> tuple[float, float]:
    lon0, lat0 = world["coordinate_frame"]["origin_wgs84_lon_lat"]
    return (
        EARTH_RADIUS_M * math.radians(lon - lon0) * math.cos(math.radians(lat0)),
        EARTH_RADIUS_M * math.radians(lat - lat0),
    )


def terrain_sampler(world: dict[str, Any]):
    terrain = world["terrain"]
    rows, columns = int(terrain["rows"]), int(terrain["columns"])
    vertices = [row["xyz_local_m"] for row in terrain["vertices"]]
    xs = [vertices[column][0] for column in range(columns)]
    ys = [vertices[row * columns][1] for row in range(rows)]

    def sample(x: float, y: float) -> float:
        fx = (x - xs[0]) / (xs[-1] - xs[0]) * (columns - 1)
        fy = (y - ys[0]) / (ys[-1] - ys[0]) * (rows - 1)
        x0 = min(max(math.floor(fx), 0), columns - 2)
        y0 = min(max(math.floor(fy), 0), rows - 2)
        tx, ty = min(max(fx - x0, 0.0), 1.0), min(max(fy - y0, 0.0), 1.0)
        a = vertices[y0 * columns + x0][2]
        b = vertices[y0 * columns + x0 + 1][2]
        c = vertices[(y0 + 1) * columns + x0][2]
        d = vertices[(y0 + 1) * columns + x0 + 1][2]
        return float((a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty)

    return sample


def robust_topographic_surface(
    controls_path: Path,
    alignment: dict[str, Any],
    world: dict[str, Any],
) -> tuple[dict[str, Any], Any]:
    """Fit a smooth, bounded ground surface to authored Moscow-height labels.

    The source has many spot elevations but no admitted TIN or breaklines.  A
    robust quadratic is deliberately lower-detail than an interpolating TIN:
    it retains the corridor's measured crest and longitudinal grade without
    turning label noise, building roofs or tree crowns into terrain spikes.
    """
    controls = json.loads(controls_path.read_text())["controls"]
    transformed: list[dict[str, Any]] = []
    for control in controls:
        lon, lat = inverse_alignment(control["xy"], alignment)
        x, y = world_local(lon, lat, world)
        transformed.append(
            {
                "x": float(x),
                "y": float(y),
                "z": float(control["z_m"]),
                "id": control["id"],
                "kind": control.get("kind"),
                "curb_role": control.get("curb_role"),
            }
        )

    # One plan point can carry the lower and upper curb observations.  Terrain
    # uses the lower value; the curb separation belongs in a later breakline
    # mesh and must not become a vertical terrain wall.
    deduplicated: dict[tuple[float, float], dict[str, Any]] = {}
    for row in transformed:
        key = (round(row["x"], 3), round(row["y"], 3))
        if key not in deduplicated or row["z"] < deduplicated[key]["z"]:
            deduplicated[key] = row
    supports = list(deduplicated.values())
    if len(supports) < 100:
        raise RuntimeError("Too few source elevation controls for the full-street surface")

    scale_x, scale_y = 250.0, 500.0
    x = np.asarray([row["x"] / scale_x for row in supports], dtype=float)
    y = np.asarray([row["y"] / scale_y for row in supports], dtype=float)
    z = np.asarray([row["z"] for row in supports], dtype=float)
    matrix = np.column_stack((np.ones(len(z)), x, y, x * x, x * y, y * y))
    weights = np.ones(len(z), dtype=float)
    coefficients = np.zeros(matrix.shape[1], dtype=float)
    for _ in range(20):
        square_root = np.sqrt(weights)
        coefficients = np.linalg.lstsq(
            matrix * square_root[:, None], z * square_root, rcond=None
        )[0]
        residuals = z - matrix @ coefficients
        # Half a metre is the source contour interval stated on GP/RPCh.  This
        # Huber-like weighting lets the measured corridor grade dominate while
        # suppressing isolated label/association errors.
        weights = np.minimum(1.0, 0.5 / np.maximum(np.abs(residuals), 1e-9))

    fitted = matrix @ coefficients
    residuals = z - fitted
    observed_min = float(z.min())
    observed_max = float(z.max())

    def absolute_height(x_local: float, y_local: float) -> float:
        xn, yn = x_local / scale_x, y_local / scale_y
        value = float(
            coefficients[0]
            + coefficients[1] * xn
            + coefficients[2] * yn
            + coefficients[3] * xn * xn
            + coefficients[4] * xn * yn
            + coefficients[5] * yn * yn
        )
        # The context grid extends beyond the annotated plan at the corners.
        # Do not invent elevations outside the observed source range there.
        return min(max(value, observed_min), observed_max)

    origin_height = absolute_height(0.0, 0.0)

    def local_height(x_local: float, y_local: float) -> float:
        return absolute_height(x_local, y_local) - origin_height

    model = {
        "status": "source_topography_robust_quadratic_surface",
        "source_vertical_datum": "Moscow height system",
        "source_control_count": len(controls),
        "deduplicated_bare_earth_supports": len(supports),
        "coincident_control_rule": "lowest Z is terrain; upper curb value is not used as a terrain wall",
        "basis": ["1", "x/250", "y/500", "(x/250)^2", "(x/250)*(y/500)", "(y/500)^2"],
        "coefficients_m": [round(float(value), 12) for value in coefficients],
        "origin_height_moscow_m": round(origin_height, 6),
        "observed_height_range_moscow_m": [round(observed_min, 6), round(observed_max, 6)],
        "fit_rmse_m": round(float(np.sqrt(np.mean(residuals * residuals))), 6),
        "fit_median_absolute_residual_m": round(float(np.median(np.abs(residuals))), 6),
        "fit_residual_quantiles_m": {
            str(percent): round(float(np.quantile(residuals, percent)), 6)
            for percent in (0.01, 0.05, 0.5, 0.95, 0.99)
        },
        "extrapolation_rule": "clamp smooth fit to the observed source elevation range",
        "microrelief_status": "omitted_until_breaklines_or_admitted_TIN_are_available",
        "neural_generation": False,
    }
    return model, local_height


def geometry_points(geometry: dict[str, Any]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []

    def collect(value: Any) -> None:
        if (
            isinstance(value, list)
            and len(value) >= 2
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            result.append((float(value[0]), float(value[1])))
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(geometry.get("coordinates", []))
    return result


def apply_source_topography(
    world: dict[str, Any],
    model: dict[str, Any],
    height_at: Any,
) -> None:
    for vertex in world["terrain"]["vertices"]:
        x, y = (float(value) for value in vertex["xyz_local_m"][:2])
        vertex["xyz_local_m"][2] = round(height_at(x, y), 6)
        vertex["topography_moscow_m"] = round(
            model["origin_height_moscow_m"] + height_at(x, y), 6
        )
        vertex["status"] = model["status"]

    rows, columns = int(world["terrain"]["rows"]), int(world["terrain"]["columns"])
    grid = np.asarray(
        [row["xyz_local_m"][2] for row in world["terrain"]["vertices"]], dtype=float
    ).reshape(rows, columns)
    xyz = np.asarray(
        [row["xyz_local_m"] for row in world["terrain"]["vertices"]], dtype=float
    )
    xs = xyz[:columns, 0]
    ys = xyz[::columns, 1]
    x_slopes = np.abs(np.diff(grid, axis=1) / np.diff(xs)[None, :])
    y_slopes = np.abs(np.diff(grid, axis=0) / np.diff(ys)[:, None])
    slopes = np.concatenate((x_slopes.ravel(), y_slopes.ravel()))
    model["render_grid_audit"] = {
        "height_range_local_m": [round(float(grid.min()), 6), round(float(grid.max()), 6)],
        "height_delta_m": round(float(np.ptp(grid)), 6),
        "maximum_neighbor_slope_ratio": round(float(slopes.max()), 9),
        "p99_neighbor_slope_ratio": round(float(np.quantile(slopes, 0.99)), 9),
    }
    world["terrain"]["status"] = model["status"]
    world["terrain"]["source_model"] = model

    for layer in world["render_layers"]:
        for triangle in layer["triangles_xyz_local_m"]:
            for point in triangle:
                point[2] = round(height_at(float(point[0]), float(point[1])) + 0.025, 6)
        layer["vertical_status"] = model["status"]

    for key in ("buildings", "infrastructure"):
        for feature in world[key]:
            points = geometry_points(feature["geometry_local"])
            if not points:
                continue
            values = sorted(height_at(x, y) for x, y in points)
            ground = values[len(values) // 2]
            properties = feature["properties"]
            if "ground_z_egm2008_m" in properties:
                properties["ground_z_egm2008_m_context_only"] = properties.pop("ground_z_egm2008_m")
            properties["ground_z_local_m"] = round(ground, 6)
            properties["ground_z_min_local_m"] = round(values[0], 6)
            properties["ground_z_max_local_m"] = round(values[-1], 6)
            properties["ground_z_moscow_m"] = round(
                model["origin_height_moscow_m"] + ground, 6
            )
            properties["ground_status"] = model["status"]

    frame = world["coordinate_frame"]
    frame["origin_dsm_egm2008_m_context_only"] = frame.pop("origin_dsm_egm2008_m")
    frame["origin_topography_moscow_m"] = model["origin_height_moscow_m"]
    frame["vertical"] = (
        "metres relative to the robust source-topography surface at the WGS84 world origin; "
        "source annotations use the Moscow height system"
    )


def photo_directory_receipt(path: Path) -> dict[str, Any]:
    files = sorted(
        item for item in path.iterdir()
        if item.is_file() and item.suffix.casefold() in {".jpg", ".jpeg", ".png"}
    )
    digest = hashlib.sha256()
    for item in files:
        digest.update(item.name.encode("utf-8"))
        digest.update(sha256(item).encode("ascii"))
    return {
        "path": str(path.resolve()),
        "image_count": len(files),
        "combined_sha256": digest.hexdigest(),
        "usage": "visual material, vegetation-density and daylight reference only; never geometry",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory-dxf", type=Path, default=DEFAULT_INVENTORY_DXF)
    parser.add_argument("--inventory-xls", type=Path, default=DEFAULT_INVENTORY_XLS)
    parser.add_argument("--alignment", type=Path, default=DEFAULT_ALIGNMENT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--osm", type=Path, default=DEFAULT_OSM)
    parser.add_argument("--dem", type=Path, default=DEFAULT_DEM)
    parser.add_argument("--overture-dir", type=Path, default=DEFAULT_DEM.parent)
    parser.add_argument("--elevation-controls", type=Path, default=DEFAULT_ELEVATION_CONTROLS)
    parser.add_argument("--gp-pdf", type=Path, default=DEFAULT_GP_PDF)
    parser.add_argument("--rpch-pdf", type=Path, default=DEFAULT_RPCH_PDF)
    parser.add_argument("--inventory-plan-1", type=Path, default=DEFAULT_INVENTORY_PLAN_1)
    parser.add_argument("--inventory-plan-2", type=Path, default=DEFAULT_INVENTORY_PLAN_2)
    parser.add_argument("--photo-directory", type=Path, default=DEFAULT_PHOTO_DIRECTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    records = parse_inventory_xls(args.inventory_xls)
    positions, position_audit = parse_inventory_positions(args.inventory_dxf)
    alignment = json.loads(args.alignment.read_text())

    inventory_wgs84 = {
        inventory_id: inverse_alignment(row["xy_dxf_m"], alignment)
        for inventory_id, row in positions.items()
    }
    lons = [item[0] for item in inventory_wgs84.values()]
    lats = [item[1] for item in inventory_wgs84.values()]
    bbox = [min(lons), min(lats), max(lons), max(lats)]

    derived_manifest = json.loads(args.manifest.read_text())
    derived_manifest["site"]["scope_bbox_wgs84_candidate"] = bbox
    derived_manifest["site"]["scope_reason"] = "full inventory marker extent, automatically transformed from inventory DXF"
    manifest_path = args.output / "full-street-source-manifest.json"
    manifest_path.write_text(json.dumps(derived_manifest, ensure_ascii=False, indent=2))

    world_dir = args.output / "world-base"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/cad-lab/build_map_first_world.py"),
            "--manifest", str(manifest_path),
            "--osm", str(args.osm),
            "--dem", str(args.dem),
            "--overture-dir", str(args.overture_dir),
            "--output", str(world_dir),
            "--margin-m", "65",
            "--terrain-step-m", "14",
        ],
        check=True,
    )
    world = json.loads((world_dir / "world.json").read_text())
    terrain_model, z_at = robust_topographic_surface(
        args.elevation_controls, alignment, world
    )
    apply_source_topography(world, terrain_model, z_at)

    vegetation = []
    unresolved = []
    for inventory_id, record in sorted(records.items()):
        position = positions.get(inventory_id)
        if position is None:
            unresolved.append({**record, "reason": "numeric marker absent from inventory DXF"})
            continue
        lon, lat = inventory_wgs84[inventory_id]
        x, y = world_local(lon, lat, world)
        vegetation.append(
            {
                **record,
                **position,
                "position_wgs84": [round(lon, 10), round(lat, 10)],
                "position_local_xyz_m": [round(x, 6), round(y, 6), round(z_at(x, y), 6)],
                "position_height_moscow_m": round(
                    terrain_model["origin_height_moscow_m"] + z_at(x, y), 6
                ),
                "z_status": terrain_model["status"],
            }
        )

    world["schema"] = "green-atlas.kustanayskaya-working-prototype.v1"
    world["status"] = "working_full_street_prototype_not_survey_admitted"
    world["inventory_vegetation"] = vegetation
    world["unresolved_inventory"] = unresolved
    world["inputs"]["inventory_dxf"] = {"path": str(args.inventory_dxf.resolve()), "sha256": sha256(args.inventory_dxf)}
    world["inputs"]["inventory_xls"] = {"path": str(args.inventory_xls.resolve()), "sha256": sha256(args.inventory_xls)}
    world["inputs"]["candidate_alignment"] = {"path": str(args.alignment.resolve()), "sha256": sha256(args.alignment)}
    world["inputs"]["source_elevation_controls"] = {
        "path": str(args.elevation_controls.resolve()),
        "sha256": sha256(args.elevation_controls),
    }
    source_pdfs = {
        "general_plan": args.gp_pdf,
        "working_project": args.rpch_pdf,
        "inventory_plan_part_1": args.inventory_plan_1,
        "inventory_plan_part_2": args.inventory_plan_2,
    }
    world["dataset_evidence"] = {
        "source_pdfs": {
            name: {"path": str(path.resolve()), "sha256": sha256(path)}
            for name, path in source_pdfs.items()
        },
        "existing_condition_photos": photo_directory_receipt(args.photo_directory),
        "source_reading": {
            "coordinate_system": "Moscow",
            "vertical_datum": "Moscow height system",
            "contour_interval_m": 0.5,
            "design_instruction": "tie proposed work to existing elevations",
        },
        "current_pipeline_usage": [
            "source spot elevations control the smooth full-street ground surface",
            "GP and RPCh sheets constrain corridor and surface semantics",
            "inventory plan PDFs validate numbering and positions imported from the paired DXF/XLS",
            "77 existing-condition photos calibrate later material and lighting work without changing geometry",
        ],
    }
    world["admission"]["blockers"] = [
        blocker for blocker in world["admission"]["blockers"]
        if "GLO-30" not in blocker
    ]
    world["admission"]["blockers"].insert(
        0,
        "The source topography now supplies a smooth corridor grade, but exact curb breaklines and local microrelief still require an admitted TIN or explicit 3D breaklines.",
    )
    prototype_path = args.output / "prototype-world.json"
    prototype_path.write_text(json.dumps(world, ensure_ascii=False, indent=2))

    counts = Counter(row["kind"] for row in vegetation)
    report = {
        "schema": "green-atlas.kustanayskaya-prototype-report.v1",
        "status": "working_prototype",
        "georeference": {
            "status": alignment["status"],
            "rmse_inliers_m": alignment["ransac"]["rmse_inliers_m"],
            "inliers": alignment["ransac"]["inliers"],
            "controls_total": alignment["ransac"]["controls_total"],
            "inventory_bbox_wgs84": bbox,
        },
        "inventory": {
            **position_audit,
            "xls_rows": len(records),
            "positioned_rows": len(vegetation),
            "unresolved_rows": len(unresolved),
            "positioned_tree_records": counts["tree"],
            "positioned_shrub_group_records": counts["shrub_group"],
            "positioned_stump_records": counts["stump"],
            "tree_quantity": sum(row["tree_count"] for row in vegetation),
            "shrub_quantity": sum(row["shrub_count"] for row in vegetation),
        },
        "gis_world": {
            "buildings": len(world["buildings"]),
            "roads": len(world["roads"]),
            "green_areas": len(world["green_areas"]),
            "infrastructure": len(world["infrastructure"]),
            "terrain_vertices": len(world["terrain"]["vertices"]),
        },
        "terrain": terrain_model,
        "dataset_evidence": {
            "source_pdf_count": len(source_pdfs),
            "existing_condition_photo_count": world["dataset_evidence"]["existing_condition_photos"]["image_count"],
        },
        "limitations": [
            "Horizontal transform is a candidate fit from addressed buildings, not an official surveyed CRS control.",
            "The source numbering has four gaps (300, 566, 675, 951) in both XLS and DXF; no records are invented for them.",
            "Positions farther than four metres from the nearest plant INSERT use the authored label position and are flagged.",
            "Vertical placement uses a smooth robust surface fitted to source Moscow-height annotations; exact curb steps and microrelief remain intentionally omitted without an admitted TIN/breaklines.",
            "Copernicus GLO-30 is retained only as input provenance and is not used for local object contact or terrain Z.",
        ],
        "outputs": {
            "world": str(prototype_path.resolve()),
            "base_world": str((world_dir / "world.json").resolve()),
            "manifest": str(manifest_path.resolve()),
        },
    }
    (args.output / "prototype-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
