"""Generate a clangd database from the native build's compiler arguments.

Does not execute the build or install a bundle. One host architecture is indexed;
the production builder still qualifies both architectures. Refresh after changes
to build-macos.sh or the SDK location. No shell expansion of source text.
"""

import argparse
import json
import os
import platform
import re
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def entries(build: str, root: Path, compiler: str, sdk: str, host: str) -> list[dict]:
    source = root / "tools/autocad-bridge/native"
    autocad = Path(
        os.environ.get(
            "AUTOCAD_2027_ROOT", "/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app"
        )
    )
    variables = {
        "$source_root": str(source),
        "$sdk_root": os.environ.get(
            "OBJECTARX_SDK_ROOT", str(root / ".local/objectarx-2027")
        ),
        "$frameworks": str(autocad / "Contents/Frameworks"),
    }

    def expand(token):
        for key, value in variables.items():
            token = token.replace(key, value)
        if "$" in token:
            raise ValueError(f"Unknown build variable in compiler option: {token}")
        return token

    commands = re.findall(r"xcrun clang\+\+.*?(?=\n\n|$)", build, re.DOTALL)
    result = []
    for command in commands:
        tokens = shlex.split(command.replace("\\\n", " "))[2:]
        options = [compiler, "-arch", host, "-isysroot", sdk]
        files = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in {"-I", "-F", "-include"}:
                options.extend([token, expand(tokens[index + 1])])
                index += 2
                continue
            if token in {"-arch", "-o", "-L", "-framework"}:
                index += 2
                continue
            if token.startswith(
                ("-std=", "-mmacosx-version-min=", "-O", "-D", "-W", "-f")
            ):
                options.append(expand(token))
            if token.startswith("$source_root/") and token.endswith((".cpp", ".mm")):
                files.append(expand(token))
            index += 1
        result.extend(
            {"directory": str(root), "file": file, "arguments": [*options, "-c", file]}
            for file in files
        )
    if not result or len({entry["file"] for entry in result}) != len(result):
        raise ValueError("Build source list is empty or ambiguous")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="New output directory"
    )
    args = parser.parse_args()
    compiler = subprocess.check_output(
        ["xcrun", "--find", "clang++"], text=True
    ).strip()
    sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
    result = entries(
        (ROOT / "tools/autocad-bridge/build-macos.sh").read_text(),
        ROOT,
        compiler,
        sdk,
        platform.machine(),
    )
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "compile_commands.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(f"Indexed compilation commands: {len(result)}; no build/install performed")


if __name__ == "__main__":
    main()
