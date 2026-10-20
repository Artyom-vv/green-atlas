"""Executable layer-0 and nested-XREF policy shared by capture and display."""

import subprocess
from pathlib import Path


def test_instance_layer_inheritance_preserves_namespaces(tmp_path: Path) -> None:
    native = Path(__file__).parent
    executable = tmp_path / "instance-layer"
    subprocess.run(
        [
            "c++",
            "-std=c++17",
            str(native / "test_instance_layer.cpp"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([str(executable)], check=True)
