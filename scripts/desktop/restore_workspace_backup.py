"""Restore a verified recovery point without overwriting an existing workspace."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/api"))
    from app.projects.workspace_restore import restore_workspace_backup

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("destination", type=Path, help="New or empty directory")
    args = parser.parse_args()
    print(restore_workspace_backup(args.backup, args.destination))


if __name__ == "__main__":
    main()
