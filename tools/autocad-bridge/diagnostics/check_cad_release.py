"""Run the product CAD writer on an explicitly captured archive, never a source DWG."""
import argparse
import json
from hashlib import sha256
from pathlib import Path

from app.exporting.cad_contracts import CadPlant
from app.exporting.cad_process import write_cad_release
from app.native_query.process_contracts import (
    CadPackageFile,
    NativeInputPackage,
    NativeQueryProcessConfig,
)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--architecture", choices=("native", "x86_64"), default="native")
    args = parser.parse_args()
    # Historical experiments predate the display-SHA binding. This diagnostic
    # tests only the writer; it never admits this archive into a user project.
    receipt = json.loads((args.package / "session.json").read_bytes())
    package = NativeInputPackage(args.package.resolve(), receipt["entry"], tuple(
        CadPackageFile(item["path"], item["sha256"], item["bytes"]) for item in receipt["files"]
    ))
    from app.exporting.cad_writer import _autocad_installation
    core, template = _autocad_installation()
    import plistlib
    info = plistlib.loads((args.worker / "Contents/Info.plist").read_bytes())
    config = NativeQueryProcessConfig(
        core,
        args.worker.resolve(), args.jobs.resolve(),
        sha256((args.worker / "Contents/MacOS/GreenAtlasBridge").read_bytes()).hexdigest(),
        info["CFBundleShortVersionString"],
        template,
        architecture=args.architecture, timeout_seconds=180,
    )
    result = write_cad_release(package, (
        CadPlant(id="aa-11", kind="shrub", x=1.25, y=2.5, radius=.5),
        CadPlant(id="bb-22", kind="tree", x=3.25, y=4.5, radius=1.5),
    ), receipt["units_code"], config)
    print(json.dumps({"receipt": result.receipt.model_dump(mode="json"),
                      "files": {name: len(content) for name, content in result.files.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
