"""Freeze and select KartaView imagery as appearance evidence for a map-first world.

Street images never become geometry.  The manifest records their WGS84 camera
positions and headings only so a reviewer can verify surfaces, facades, and
vegetation visible near the site.  Downloads are restricted to KartaView's
documented public image hosts and carry the required CC-BY-SA attribution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_METADATA = ROOT / ".runtime/kartaview-kustanayskaya-20260920"
DEFAULT_WORLD = ROOT / ".runtime/map-first-world-20260920/world.json"
DEFAULT_OUTPUT = DEFAULT_METADATA / "evidence"
ALLOWED_IMAGE_HOSTS = {"storage13.openstreetcam.org", "cdn.kartaview.org"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_quantiles(rows: list[dict], count: int) -> list[dict]:
    if len(rows) <= count:
        return rows
    indices = {round(index * (len(rows) - 1) / (count - 1)) for index in range(count)}
    return [rows[index] for index in sorted(indices)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--frames", type=int, default=9)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    image_dir = args.output / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    world = json.loads(args.world.read_text())
    bbox = world["scope"]["project_bbox_wgs84_candidate"]
    lon0, lat0 = world["coordinate_frame"]["origin_wgs84_lon_lat"]
    radius = float(world["coordinate_frame"]["earth_radius_m"])
    metadata_paths = sorted(args.metadata.glob("sequence-*-page-*.json"))
    if not metadata_paths:
        raise FileNotFoundError("No frozen KartaView sequence pages")

    rows = []
    for path in metadata_paths:
        payload = json.loads(path.read_text())
        if int(payload["status"]["apiCode"]) != 600:
            raise ValueError(f"KartaView page failed: {path}")
        rows.extend(payload["result"]["data"])
    unique = {str(row["id"]): row for row in rows}
    rows = sorted(unique.values(), key=lambda row: int(row["sequenceIndex"]))
    inside = [
        row for row in rows
        if bbox[0] <= float(row["lng"]) <= bbox[2]
        and bbox[1] <= float(row["lat"]) <= bbox[3]
    ]
    if not inside:
        raise ValueError("No KartaView frames fall inside the project bbox")
    selected = select_quantiles(inside, args.frames)

    entries = []
    for order, row in enumerate(selected, start=1):
        lon, lat = float(row["lng"]), float(row["lat"])
        x = radius * math.radians(lon - lon0) * math.cos(math.radians(lat0))
        y = radius * math.radians(lat - lat0)
        url = str(row.get("fileurlLTh") or row.get("imageLthUrl") or "")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_IMAGE_HOSTS:
            raise ValueError(f"Unexpected KartaView image URL: {url}")
        filename = f"{order:02d}_idx{int(row['sequenceIndex']):03d}_heading{round(float(row['heading'])):03d}_{row['id']}.jpg"
        image_path = image_dir / filename
        if args.download:
            request = urllib.request.Request(url, headers={"User-Agent": "GreenAtlas/1.0 KartaView evidence capture"})
            with urllib.request.urlopen(request, timeout=60) as response, image_path.open("wb") as stream:
                stream.write(response.read())
        entries.append({
            "photo_id": str(row["id"]),
            "sequence_id": str(row["sequenceId"]),
            "sequence_index": int(row["sequenceIndex"]),
            "shot_date": row.get("shotDate"),
            "camera": {
                "wgs84_lon_lat": [lon, lat],
                "local_xy_m": [round(x, 6), round(y, 6)],
                "heading_deg": float(row["heading"]),
                "projection": row.get("projection"),
                "field_of_view_deg": float(row.get("fieldOfView") or 0),
                "gps_accuracy_source_value": row.get("gpsAccuracy"),
            },
            "source_url": url,
            "local_image": str(image_path.resolve()) if image_path.exists() else None,
            "sha256": sha256(image_path) if image_path.exists() else None,
            "evidence_role": "appearance_and_presence_review_only",
            "forbidden_uses": [
                "survey coordinate control",
                "metric curb or road boundary extraction without independent calibration",
                "unreviewed object insertion",
            ],
        })

    manifest = {
        "schema": "green-atlas.kartaview-evidence.v1",
        "status": "captured_appearance_evidence_not_geometry",
        "world": {"path": str(args.world.resolve()), "sha256": sha256(args.world)},
        "metadata_pages": [
            {"path": str(path.resolve()), "sha256": sha256(path)} for path in metadata_paths
        ],
        "selection": {
            "project_bbox_wgs84": bbox,
            "frames_in_bbox": len(inside),
            "selected_frames": len(entries),
            "sequence_indices_in_bbox": [int(inside[0]["sequenceIndex"]), int(inside[-1]["sequenceIndex"])],
            "method": "uniform sequence-index quantiles inside project bbox",
        },
        "license": {
            "name": "Creative Commons Attribution-ShareAlike 4.0 International",
            "url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "attribution": "© Grab and KartaView Contributors",
            "terms": "https://kartaview.org/terms",
        },
        "frames": entries,
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    downloaded = [Path(row["local_image"]) for row in entries if row["local_image"]]
    if downloaded:
        contact_sheet = args.output / "contact-sheet.jpg"
        subprocess.run([
            "magick", "montage", *[str(path) for path in downloaded],
            "-auto-orient", "-thumbnail", "520x340>", "-tile", "3x3",
            "-geometry", "+8+28", "-background", "#202428", "-fill", "white",
            "-pointsize", "16", "-set", "label", "%t", str(contact_sheet),
        ], check=True)

    receipt = {
        "schema": "green-atlas.kartaview-evidence-receipt.v1",
        "manifest": {"path": str(manifest_path.resolve()), "sha256": sha256(manifest_path)},
        "frames_in_bbox": len(inside),
        "selected_frames": len(entries),
        "downloaded_frames": len(downloaded),
        "status": manifest["status"],
    }
    (args.output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
