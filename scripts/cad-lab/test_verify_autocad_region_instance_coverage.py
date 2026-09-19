import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("verify_autocad_region_instance_coverage.py")
SPEC = importlib.util.spec_from_file_location("region_instance_verifier", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value))
    return path


def fixtures(tmp_path: Path) -> tuple[Path, Path, Path]:
    inventory = {
        "source_sha256": "source-hash",
        "acis": [{"handle": "A", "layout": "definition"}],
        "acis_instances": [
            {
                "block": "definition",
                "chain": [{"handle": "I"}],
                "matrix": [
                    1, 0, 0, 0,
                    0, 1, 0, 0,
                    0, 0, 1, 0,
                    10, 20, 0, 1,
                ],
            }
        ],
    }
    definition = {
        "regions": [
            {
                "source_layer": "SOURCE_A",
                "loops": [{"coordinates": [[0, 0, 0], [1, 0, 0], [0, 0, 0]]}],
            }
        ]
    }
    full = {
        "source": {"sha256": "source-hash"},
        "regions": [
            {
                "handle": "A",
                "instance_chain": ["I"],
                "loops": [
                    {"coordinates": [[10, 20, 0], [11, 20, 0], [10, 20, 0]]}
                ],
            }
        ],
    }
    return (
        write(tmp_path / "inventory.json", inventory),
        write(tmp_path / "definitions.json", definition),
        write(tmp_path / "full.json", full),
    )


def test_accepts_matching_chain_and_world_coordinates(tmp_path: Path) -> None:
    report = MODULE.verify(*fixtures(tmp_path))
    assert report["passed"] is True
    assert report["counts"]["compared_regions"] == 1


def test_rejects_coordinate_mismatch(tmp_path: Path) -> None:
    inventory, definition, full = fixtures(tmp_path)
    document = json.loads(full.read_text())
    document["regions"][0]["loops"][0]["coordinates"][1][0] = 12
    full.write_text(json.dumps(document))
    report = MODULE.verify(inventory, definition, full)
    assert report["passed"] is False
    assert report["failures"][0]["reason"].startswith("world coordinates")
