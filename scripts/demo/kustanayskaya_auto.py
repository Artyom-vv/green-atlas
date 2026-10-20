"""Preview automatic planting in a prepared local Green Atlas project.

This invokes the same application services as the UI. It never reads DWG itself.
Saving to the desktop workspace requires an explicit project ID confirmation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from app.automatic_placement import (
        MAX_AUTOMATIC_PLACEMENT_COUNT,
        AutomaticPlacementRequest,
        preview_automatic_placement,
    )
    from app.composition import create_runtime
    from app.planning.changes.contracts import PlanChangeSetApplyRequest

    parser = argparse.ArgumentParser(description="Проверка автопосадки на Кустанайской")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project-id")
    parser.add_argument("--zone-id")
    parser.add_argument("--kind", choices=("tree", "shrub", "mixed"), default="shrub")
    parser.add_argument(
        "--near", choices=("building", "road", "area"), default="building"
    )
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument(
        "--list", action="store_true", help="Показать проекты и участки"
    )
    parser.add_argument("--apply", action="store_true", help="Сохранить после проверки")
    parser.add_argument(
        "--confirm-project-id", help="Для сохранения в рабочий проект повторите его ID"
    )
    args = parser.parse_args()

    if not 1 <= args.count <= MAX_AUTOMATIC_PLACEMENT_COUNT:
        parser.error(f"Количество должно быть от 1 до {MAX_AUTOMATIC_PLACEMENT_COUNT}")
    database = args.database.resolve()
    if not database.is_file():
        parser.error("База проекта не найдена")
    if args.apply and (
        not args.project_id or args.confirm_project_id != args.project_id
    ):
        parser.error(
            "Для сохранения укажите --project-id и повторите его в --confirm-project-id"
        )

    runtime = create_runtime(database)
    projects = runtime.application.list_projects()
    if args.list:
        print(
            json.dumps(
                {
                    "projects": [
                        {
                            "id": item.id,
                            "name": item.name,
                            "map_ready": bool(detail.map_ready),
                            "zones": [
                                {"id": zone.id, "label": zone.label}
                                for zone in detail.planting_zones
                            ],
                        }
                        for item in projects
                        for detail in [
                            runtime.application.get(item.id, lightweight=True)
                        ]
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    summary = next((item for item in projects if item.id == args.project_id), None)
    if summary is None:
        if not args.project_id and len(projects) == 1:
            summary = projects[0]
        else:
            print(
                json.dumps(
                    {
                        "projects": [
                            {"id": item.id, "name": item.name} for item in projects
                        ]
                    },
                    ensure_ascii=False,
                )
            )
            parser.error("Укажите --project-id из списка")
    project = runtime.application.get(summary.id)
    if not project.map_ready or project.plan is None:
        parser.error("Подготовьте карту и план проекта через AutoCAD и Green Atlas")
    zones = {zone.id for zone in project.planting_zones}
    if args.zone_id not in zones:
        print(json.dumps({"zones": sorted(zones)}, ensure_ascii=False))
        parser.error("Укажите --zone-id из списка")

    before = project.plan.model_dump_json()
    result = preview_automatic_placement(
        runtime.application,
        project.id,
        AutomaticPlacementRequest(
            base_plan_version=project.plan.version,
            zone_id=args.zone_id,
            plant_kind=args.kind,
            target_count=args.count,
            near=args.near,
        ),
    )
    change = result.change_set
    unchanged = runtime.application.get(project.id).plan
    if unchanged is None or unchanged.model_dump_json() != before:
        raise RuntimeError("Предпросмотр изменил сохранённый план")
    additions = change.additions if change else []
    report = {
        "project_id": project.id,
        "zone_id": args.zone_id,
        "near": args.near,
        "kind": args.kind,
        "requested": args.count,
        "found": result.found,
        "can_apply": bool(change and change.can_apply),
        "species": result.species_revision_ids,
        "saved": False,
    }
    if args.apply and change and change.can_apply and additions:
        receipt = runtime.application.apply_change_set(
            project.id,
            PlanChangeSetApplyRequest(
                preview_id=change.id,
                digest=change.digest,
                base_plan_version=change.base_plan_version,
            ),
        )
        reopened = create_runtime(database).application.get(project.id)
        if reopened.plan is None:
            raise RuntimeError("План не найден после повторного открытия")
        persisted = {item.id for item in reopened.plan.objects}
        if not set(receipt.added_ids) <= persisted:
            raise RuntimeError("Посадки не найдены после повторного открытия")
        report["saved"] = True
        report["persisted_count"] = len(receipt.added_ids)
        report["plan_version"] = reopened.plan.version
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["can_apply"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
