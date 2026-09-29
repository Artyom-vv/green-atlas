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
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.agent_memory import TaskPatch, TaskState
from app.agent_planning import prepare_placement
from app.composition import create_runtime
from app.planning.change_contracts import PlanChangeSetApplyRequest


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка автопосадки на Кустанайской")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project-id")
    parser.add_argument("--zone-id")
    parser.add_argument("--kind", choices=("tree", "shrub", "mixed"), default="shrub")
    parser.add_argument("--near", choices=("building", "road", "area"), default="building")
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--list", action="store_true", help="Показать проекты и участки")
    parser.add_argument("--apply", action="store_true", help="Сохранить после проверки")
    parser.add_argument("--confirm-project-id", help="Для сохранения в рабочий проект повторите его ID")
    args = parser.parse_args()

    database = args.database.resolve()
    if not database.is_file():
        parser.error("База проекта не найдена")
    if args.count < 1 or args.count > 500:
        parser.error("Количество должно быть от 1 до 500")
    if args.apply and (not args.project_id or args.confirm_project_id != args.project_id):
        parser.error("Для сохранения укажите --project-id и повторите его в --confirm-project-id")

    runtime = create_runtime(database)
    projects = runtime.application.list_projects()
    if args.list:
        print(json.dumps({"projects": [
            {"id": item.id, "name": item.name,
             "map_ready": bool(detail.map_ready),
             "zones": [{"id": zone.id, "label": zone.label}
                       for zone in detail.planting_zones]}
            for item in projects
            for detail in [runtime.application.get(item.id, lightweight=True)]
        ]}, ensure_ascii=False, indent=2))
        return 0
    project = next((item for item in projects if item.id == args.project_id), None)
    if project is None:
        if not args.project_id and len(projects) == 1:
            project = projects[0]
        else:
            print(json.dumps({"projects": [{"id": item.id, "name": item.name}
                                            for item in projects]}, ensure_ascii=False))
            parser.error("Укажите --project-id из списка")
    project = runtime.application.get(project.id)
    if not project.map_ready or project.plan is None:
        parser.error("Подготовьте карту и план проекта через AutoCAD и Green Atlas")
    zones = {zone.id for zone in project.planting_zones}
    if args.zone_id not in zones:
        print(json.dumps({"zones": sorted(zones)}, ensure_ascii=False))
        parser.error("Укажите --zone-id из списка")

    arrangement = {"building": "building_contour", "road": "road_edges",
                   "area": "area"}[args.near]
    before = project.plan.model_dump_json()
    task = TaskState().amended(TaskPatch(
        operation="place", scope="zones", zone_ids=[args.zone_id],
        plant_kind=args.kind, quantity=args.count, quantity_mode="target",
        arrangement=arrangement, species_mode="automatic",
    ), "demo")
    result = prepare_placement(runtime.application, project.id, task)
    change = result.get("change_set") or {}
    if runtime.application.get(project.id).plan.model_dump_json() != before:
        raise RuntimeError("Предпросмотр изменил сохранённый план")
    additions = change.get("additions", [])
    report = {
        "project_id": project.id,
        "zone_id": args.zone_id,
        "near": args.near,
        "kind": args.kind,
        "requested": args.count,
        "found": result["found"],
        "can_apply": bool(change.get("can_apply")),
        "species": sorted({item["species_revision_id"] for item in additions}),
        "saved": False,
    }
    if args.apply and change.get("can_apply") and additions:
        receipt = runtime.application.apply_change_set(
            project.id,
            PlanChangeSetApplyRequest(
                preview_id=change["id"], digest=change["digest"],
                base_plan_version=change["base_plan_version"],
            ),
        )
        reopened = create_runtime(database).application.get(project.id)
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
