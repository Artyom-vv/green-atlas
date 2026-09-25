"""Remove developer separators only from known demo labels, preserving the plan."""

import argparse

from app.composition import create_runtime


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    runtime = create_runtime(args.database)
    try:
        project = runtime.application.get(args.project)
        before = project.plan.model_dump_json() if project.plan else None
        changed = False
        if project.name == "Кустанайская · DXF · native":
            project.name = "Кустанайская"
            changed = True
        labels = {
            "native-north-patch": ("Северный участок · посадка", "Северный участок"),
            "native-road-control": ("Дорога · контроль", "Контроль дороги"),
        }
        for zone in project.planting_zones:
            old, new = labels.get(zone.id, (None, None))
            if zone.label == old:
                zone.label = new
                changed = True
        if changed:
            runtime.project_repository.save(project)
        saved = runtime.application.get(args.project, lightweight=True)
        assert (saved.plan.model_dump_json() if saved.plan else None) == before
        print(f"Updated labels: {changed}; plan unchanged; state={saved.state_version}")
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
