"""Resolve local appearance resources and freeze them outside the project store."""

import json
import os
import shutil
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.projects.resource_scope import register_project_resource
from app.rendering.resource_settings import configured_resource_root
from app.scene.contracts import ScenePlantObject, SceneSnapshot


def file_sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resource_root(variable: str, relative: str) -> Path:
    configured = configured_resource_root(variable)
    if configured is not None:
        return configured
    for parent in Path(__file__).resolve().parents:
        root = parent / relative
        if root.is_dir():
            return root
    label = "Botaniq" if variable == "GREEN_ATLAS_BOTANIQ_ROOT" else "PBR"
    raise ValueError(
        f"Ресурсы {label} не найдены. Откройте «Компоненты» и выберите каталог библиотеки."
    )


def plant_asset(row: ScenePlantObject, profile: dict[str, Any]) -> tuple[str, str, str]:
    genus = (row.scientific_name or "").split(" ")[0]
    if row.kind == "shrub":
        return (
            "Syringa-vulgaris",
            profile["shrub_asset"],
            ("genus_matched" if genus == "Syringa" else "morphology_surrogate"),
        )
    key = profile["species_map"].get(genus)
    status = "genus_matched"
    if not key:
        key = "Picea-abies" if row.crown_shape == "conical" else "Tilia-europaea"
        status = "morphology_surrogate"
    elif genus in profile["surrogate_genera"]:
        status = "morphology_surrogate"
    variants = profile["tree_assets"][key]
    index = int(sha256(row.object_id.encode()).hexdigest()[:8], 16) % len(variants)
    return key, variants[index], status


def prepare_appearance(
    snapshot: SceneSnapshot,
    directory: Path,
    context_markers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profile_path = Path(__file__).with_name("native_style.json")
    profile = json.loads(profile_path.read_bytes())
    botaniq = resource_root(
        "GREEN_ATLAS_BOTANIQ_ROOT",
        ".runtime/botaniq-68-inspection/selected/botaniq_full",
    )
    pbr = resource_root(
        "GREEN_ATLAS_RENDER_PBR_ROOT", ".runtime/cad-vegetation-20260919/pbr"
    )
    plants = []
    context_plants = []
    model_paths = {"blends_280/Library_Botaniq_Materials.blend"}
    for row in snapshot.objects:
        key, asset_relative, status = plant_asset(row, profile)
        model_paths.add(asset_relative)
        plants.append(
            {
                "object_id": row.object_id,
                "asset_key": key,
                "relative_path": "botaniq/" + asset_relative,
                "match_status": status,
            }
        )
    for marker in context_markers or []:
        # The marker fixes position only. The selected broadleaf collection is
        # an explicitly illustrative model, not an inferred inventory species.
        placeholder = ScenePlantObject(
            object_id=marker["object_id"],
            kind="tree",
            local_x=marker["local_x"],
            local_y=marker["local_y"],
            crown_shape="round",
            canopy_radius_min_m=marker["radius_m"],
            canopy_radius_max_m=marker["radius_m"],
        )
        key, asset_relative, _status = plant_asset(placeholder, profile)
        model_paths.add(asset_relative)
        context_plants.append(
            {
                "object_id": marker["object_id"],
                "asset_key": key,
                "relative_path": "botaniq/" + asset_relative,
                "match_status": "illustrative_unknown_species",
            }
        )
    lawn = any(row.kind == "lawn" for row in snapshot.context_features)
    grass_paths = profile["grass_assets"] if lawn else []
    model_paths.update(grass_paths)
    sources = {"botaniq/" + path: botaniq / path for path in model_paths}
    for path in (botaniq / "textures").rglob("*"):
        if path.is_file():
            sources["botaniq/" + path.relative_to(botaniq).as_posix()] = path
    for roles in profile["materials"].values():
        for name in roles.values():
            sources["pbr/" + name] = pbr / name
    records: list[dict[str, Any]] = []
    for relative, path in sorted(sources.items()):
        if not path.is_file():
            raise ValueError(f"Отсутствует ресурс рендера: {path.name}")
        records.append(
            {
                "relative_path": relative,
                "sha256": file_sha(path),
                "size_bytes": path.stat().st_size,
            }
        )
    content = json.dumps(
        {"profile": sha256(profile_path.read_bytes()).hexdigest(), "files": records},
        sort_keys=True,
    ).encode()
    digest = sha256(content).hexdigest()
    bundle = directory / "bundles" / digest
    register_project_resource("asset-bundle", digest)
    for record in records:
        register_project_resource("asset-blob", record["sha256"])
    # Blends retain their relative material-library dependencies. Immutable
    # blobs are shared between bundles; no licensed binary enters Git or UI.
    for record in records:
        relative, file_digest = record["relative_path"], record["sha256"]
        target = bundle / relative
        if target.is_file() and file_sha(target) == file_digest:
            continue
        blob = directory / "blobs" / file_digest
        if not blob.is_file() or file_sha(blob) != file_digest:
            blob.parent.mkdir(parents=True, exist_ok=True)
            pending = blob.with_suffix(".pending")
            shutil.copyfile(sources[relative], pending)
            if file_sha(pending) != file_digest:
                raise ValueError("Ресурс оформления изменился во время подготовки")
            pending.replace(blob)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        try:
            os.link(blob, target)
        except OSError:
            shutil.copyfile(blob, target)
    files = [dict(**r, path=str(bundle / r["relative_path"])) for r in records]
    material_registry = {
        key: {
            "maps": {
                role: {
                    "path": str(bundle / "pbr" / name),
                    "expected_sha256": next(
                        r["sha256"]
                        for r in records
                        if r["relative_path"] == "pbr/" + name
                    ),
                }
                for role, name in roles.items()
            }
        }
        for key, roles in profile["materials"].items()
    }
    return {
        "profile": profile["profile"],
        "lighting": profile["lighting"],
        "bundle_sha256": digest,
        "files": files,
        "plants": [dict(**r, path=str(bundle / r["relative_path"])) for r in plants],
        "context_plants": [
            dict(**r, path=str(bundle / r["relative_path"])) for r in context_plants
        ],
        "texture_root": str(bundle / "botaniq" / "textures"),
        "grass_paths": [str(bundle / "botaniq" / p) for p in grass_paths],
        "materials": material_registry,
    }
