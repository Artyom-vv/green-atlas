"""Verify the qualified Python runtime and ezdxf accelerators before serving."""

from __future__ import annotations

import platform
import sys
from pathlib import Path


def main() -> int:
    expected = (Path(__file__).resolve().parents[1] / ".python-version").read_text(
        encoding="utf-8"
    ).strip()
    actual = platform.python_version()
    if platform.python_implementation() != "CPython" or actual != expected:
        print(
            f"API requires CPython {expected}; found {platform.python_implementation()} "
            f"{actual}. Recreate the environment from apps/api/.python-version "
            "and uv.lock before starting the API.",
            file=sys.stderr,
        )
        return 1

    import ezdxf

    if not ezdxf.options.use_c_ext:
        print(
            "ezdxf C extensions are not active. Install the binary wheel from "
            "uv.lock and check EZDXF_DISABLE_C_EXT / ezdxf configuration. "
            "The API will not silently use the slower pure-Python fallback.",
            file=sys.stderr,
        )
        return 1
    print(
        f"API runtime verified: CPython {actual}; ezdxf {ezdxf.__version__}; "
        "C extensions active."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
