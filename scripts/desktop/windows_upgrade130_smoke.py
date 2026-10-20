"""Check the pinned private 0.1.130 setup -> current update under an ordinary SID."""

from pathlib import Path
from shutil import copy2, copytree

from windows_upgrade_smoke import REPO, main, project_probe


def preserved_workspace_probe(
    package: Path, data: Path, environment: dict[str, str], *, create: bool
):
    """Use a disposable copy of the 77-plant project across both installed EXEs."""
    if create:
        source = REPO / ".runtime/m002-fixture-130-01/source/Workspace"
        if not (source / "projects.sqlite3").is_file():
            raise ValueError("The pinned 77-plant Workspace is unavailable")
        for entry in source.iterdir():
            if entry.is_symlink():
                raise ValueError("The Workspace copy contains a link")
            destination = data / entry.name
            if destination.exists():
                raise ValueError("The Workspace target is not empty")
            if entry.is_dir():
                copytree(entry, destination, symlinks=False)
            elif entry.is_file():
                copy2(entry, destination)
            else:
                raise ValueError("The Workspace copy contains an unknown entry")
    project, web, shutdown = project_probe(package, data, environment, create=False)
    if len(project["plan"]["objects"]) != 77 or len(project["planting_zones"]) != 1:
        raise ValueError("The 77-plant project was changed during the update")
    return project, web, shutdown


if __name__ == "__main__":
    raise SystemExit(
        main(
            upgrade_control={
                "asset": "GreenAtlas-0.1.130-Windows-x64-Setup-2.exe",
                "sha256": "4de60ea3a69b9559314b7517d978c022de05fc268738f2baed4d1d6305df20b6",
                "version": "0.1.130",
            },
            project_probe_fn=preserved_workspace_probe,
        )
    )
