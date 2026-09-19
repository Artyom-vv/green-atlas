"""Full file-backed reader check; no project writes or admission bypass."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time

from app.dxf_import.adapters import EzdxfReader


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with args.output.open("x", encoding="utf8") as stream:
        start = time.monotonic()
        result = EzdxfReader().read_prepared_file(args.source)
        features = result.geometry.feature_collection["features"]
        cable = [f for f in features if f["properties"].get("source_layer", "").split("$0$")[-1] == "Кабели"]
        report = dict(source=str(args.source), seconds=time.monotonic()-start,
                      features=len(features), cable_features=len(cable),
                      cable_with_component=sum("source_component_handle" in f["properties"] for f in cable),
                      cable_blocks=dict(Counter(f["properties"].get("source_component_block", "missing").split("$0$")[-1] for f in cable)),
                      incomplete_layers=[l.source_name for l in result.layers if not l.geometry_complete],
                      warnings=result.warnings)
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k not in {"cable_blocks", "warnings"}}, ensure_ascii=False))
