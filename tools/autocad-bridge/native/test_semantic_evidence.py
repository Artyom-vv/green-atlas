"""Executable semantic retention limits and source-bound row serialization."""

import subprocess
from pathlib import Path


def test_semantic_catalogue_preserves_rows_and_refuses_corruption(
    tmp_path: Path,
) -> None:
    native = Path(__file__).parent
    executable = tmp_path / "semantic-evidence"
    subprocess.run(
        [
            "xcrun",
            "clang++",
            "-std=c++17",
            "-Wno-deprecated-declarations",
            str(native / "test_semantic_evidence.cpp"),
            str(native / "semantic_evidence.cpp"),
            str(native / "file_io.cpp"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)
