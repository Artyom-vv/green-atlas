"""Explicit, user-delegated review of this capture; never a general classifier.

Dry-run by default. Records all 124 decisions and previous values, then uses
ordinary version-guarded HTTP mappings/recalculation without touching CAD or
plant coordinates. Ambiguous mixed layers and utility dimensions stay unknown.
"""

import argparse
import json
import time
from pathlib import Path

import httpx
from app.native_query.live_runtime import LiveBinding, binding_path
from prepare_kust_live_demo import KUST_DXF_SHA, decision

OVERRIDES = {
    "03_10004141_Проектные решения|_ГП_Граница проектирования (штриховка)":
        ("ignore", True, "Штриховка оформления территории, не препятствие; отдельные слои границы сохранены"),
    "03_10004141_Проектные решения|ДВ_ГП_П_ООТ":
        ("ignore", True, "Только 7 выносок AcDbMLeader; павильоны ООТ учитываются отдельным слоем building"),
    "03_10004141_Проектные решения|ДВ_ГП_П_Воздуховоды":
        ("restricted", True, "Физические выходы вентиляции HATCH/CIRCLE, не трасса подземной сети"),
    "03_10004141_Проектные решения|ДВ_ГП_П_Водоотводные_лотки":
        ("restricted", True, "Лотки — физические элементы покрытия; MLINE без native-ответа остаются неизвестными"),
    "00.1_10004141_Топография|Береговая линия":
        ("water", True, "Контур водного объекта; открытая линия не доказывает внутреннюю область"),
    "ИОТ2|ЭС_ВРЩ_СП":
        ("restricted", True, "Электротехническое оборудование, не газон; исходные замкнутые HATCH/полилинии сохраняют запрет"),
}
NETWORK_TYPES = {
    "Кабель электрический": "power_cable", "Кабель электрический проектный": "power_cable",
    "Водопровод": "water", "Теплосеть": "heat", "Канализация самотёчная": "sewer",
    "Кабель связи": "communication_cable", "Кабель связи проектный": "communication_cable",
    "Дренаж": "drainage", "Водосток": "drainage", "ЛЭП": "power_cable",
    "ИОТ1_КЛ НО проект.": "power_cable", "ИОТ1_КЛ НО сущ.": "power_cable",
    "ЭН_ВЛ НО проект.": "power_cable", "ЭН_ВЛ НО": "power_cable",
    "ЭС_КЛ_0.2-0.4_кВ - 4": "power_cable",
}


def reviewed(layer):
    name = layer["source_name"]
    prefix, _, leaf = name.rpartition("|")
    role, confirmed, reason = OVERRIDES.get(name, decision(name))
    if prefix == "03_10004141_Проектные решения" and leaf.startswith("ДВ_ПП_Тип0_Газон"):
        role, confirmed = "lawn", True
        reason = "Проектное покрытие газона; не существующее дерево и не доказательство допустимости посадки"
    if not confirmed:
        reason = {
            "0": "Смешанный безымянный слой: неизвестный INSERT и физические LINE/ARC/HATCH",
            "Леса и газоны": "Смешаны деревья и наземное покрытие; всему слою нельзя назначить lawn",
            "Внутреннее заполнение": "444 полилинии и 351 линия; без проверки объектов нельзя считать всё оформлением",
            "Откосы": "Рельеф не подтверждает ни запрет посадки, ни пригодность склона",
            "Граница растительности и грунта": "Граница нескольких типов поверхности, не однозначный газон",
        }.get(leaf or name, "Неоднозначный физический смысл или стадия демонтажа; не исключать автоматически")
    mapping = {"layer_id": layer["id"], "kind": role, "confirmed": confirmed,
               "visible": layer["visible"]}
    if role == "utility":
        context = layer.get("utility_context")
        if context:
            mapping["utility_context"] = context  # Never overwrite user's evidence.
        elif leaf in NETWORK_TYPES:
            mapping["utility_context"] = {
                "network_type": NETWORK_TYPES[leaf], "geometry_reference": "unknown",
                "installation": "aboveground" if leaf.startswith("ЭН_ВЛ") else "unknown",
                "review_status": "unconfirmed", "source_reference": name,
                "confirmed_by": "",
            }
        mapping["utility_axis_bindings"] = layer.get("utility_axis_bindings", [])
    return mapping, reason


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    binding = LiveBinding.model_validate_json(binding_path(args.database).read_bytes())
    if binding.session.source_sha256 != KUST_DXF_SHA:
        parser.error("Review belongs to a different CAD source")
    url = args.base_url + "/api/projects/" + binding.project_id
    with httpx.Client(timeout=240) as client:
        before = client.get(url).raise_for_status().json()
        assert before["source_file"]["content_sha256"] == binding.session.snapshot_sha256
        assert len(before["layers"]) == 124, "Review layer set changed"
        assert set(OVERRIDES) <= {l["source_name"] for l in before["layers"]}
        mappings, records = [], []
        for layer in before["layers"]:
            mapping, reason = reviewed(layer)
            mappings.append(mapping)
            records.append({"source_name": layer["source_name"], "before": layer,
                            "mapping": mapping, "basis": reason})
        receipt = {"source_sha256": KUST_DXF_SHA, "project_id": binding.project_id,
                   "before_state": before["state_version"], "before_plan": before["plan"],
                   "decisions": records, "applied": False}
        # Preserve evidence before any write; refuses to overwrite previous receipts.
        with args.receipt.open("x") as out:
            json.dump(receipt, out, ensure_ascii=False, indent=2)
        if args.apply:
            mapped = client.put(url + "/layer-mappings", json={"mappings": mappings},
                       headers={"If-Match": str(before["state_version"])}).raise_for_status().json()
            response = client.post(url + "/operations/geometry",
                                   headers={"If-Match": str(mapped["state_version"])})
            response.raise_for_status()
            operation = response.json()
            deadline = time.monotonic() + 240
            while operation["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(1)
                operation = client.get(url + "/operations/" + operation["id"]).raise_for_status().json()
            after = client.get(url).raise_for_status().json()
            receipt.update(applied=True, operation=operation, after_state=after["state_version"],
                           after_geometry=after["geometry_version"], after_plan=after["plan"])
            args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            assert operation["status"] == "completed", operation
            assert before["plan"]["objects"] == after["plan"]["objects"], "Plant objects changed"
            assert before["planting_zones"] == after["planting_zones"], "Work zones changed"
        print(json.dumps({"layers": len(records), "unconfirmed": [r["source_name"] for r in records
                         if not r["mapping"]["confirmed"]], "applied": args.apply}, ensure_ascii=False))


if __name__ == "__main__":
    main()
