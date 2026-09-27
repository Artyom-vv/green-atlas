"""Explicit Kustanayskaya demo layer decisions, NOT an automatic classifier.

Records every decision. Unknown physical layers stay unconfirmed and are local
native review areas. This script does not plant anything or declare free land.
"""

import argparse
import json
from pathlib import Path

from app.composition import create_runtime
from app.dxf_import.layer_contracts import LayerMapping
from app.native_query.live_runtime import LiveBinding, binding_path, save_binding

KUST_DXF_SHA = "36ec0f0db3a2ac88c575a9460283a2498504215a4b0daaf98aa5cb18fb2a0a8a"
LINEAR_LAYERS = frozenset(
    {
        "03_10004141_Проектные решения|ДВ_Борт_БР100.30.15",
        "03_10004141_Проектные решения|ДВ_Борт_БР100.45.18",
        "03_10004141_Проектные решения|ДВ_ГП_П_Борт_Пониженный",
        "00.1_10004141_Топография|Бортовой камень",
    }
)

# Reviewed layer-name interpretations for this fixture only. Native capability
# still has to succeed; naming a layer never certifies an area or pipe diameter.
TOPO = {
    "Здания": "building",
    "Части зданий": "building",
    "Спец сооружения": "building",
    "Павильоны": "building",
    "Навесы": "building",
    "Граница улицы": "road",
    "Бортовой камень": "road",
    "Граница площадки": "road",
    "Крыльца": "road",
    "Лестницы набережных": "road",
    "Отдельно стоящее дерево": "existing_green",
    "Полоса деревьев": "existing_green",
    "Колодцы": "restricted",
    "Люки": "restricted",
    "Светофоры": "restricted",
    "Фонтаны": "restricted",
    "Памятники": "restricted",
    "Фонари": "restricted",
    "Вентиляторы": "restricted",
    "Ограды": "restricted",
    "Столбы": "restricted",
    "Выгребные ямы": "restricted",
    "Парапеты": "restricted",
    "Вышки": "restricted",
    "Опоры контакной сети": "restricted",
    "Трубы": "utility",
    "ЛЭП": "utility",
}
ANNOTATIONS = {
    "Номер дома",
    "Пояснительные подписи",
    "Рамки",
    "Координатные кресты",
    "Подписи рамок",
    "Геодезические пункты",
    "Горизонтали",
    "Граница заказа",
    "Указатель подз коммуникаций",
    "ИОТ1_нумерация опор",
    "ЭН_Выноска труба_Моссвет",
    "Выноски ТР НО",
    "ЭС_текст",
    "ЭС_Футляры_надписи",
    "ДВ_ГП_П_ОФР_Размеры_осн",
}
PROJECT_STRUCTURES = {"ДВ_ГП_П_Павильон_ООТ", "ДВ_ГП_П_Беседка"}
PROJECT_RESTRICTED = {
    "ДВ_ГП_П_МАФ",
    "ДВ_ГП_П_ООТ",
    "ДВ_ГП_П_Постамент",
    "Подпорная стенка",
    "ДВ_ГП_П_ПО_Удерживающее",
}


def decision(name):
    prefix, _, leaf = name.rpartition("|")
    leaf = leaf or name
    if name == "_ГП_Граница проектирования" or prefix == "01_10004141_Границы работ":
        return "site_border", True, "Граница проектирования или граница работ"
    if leaf in ANNOTATIONS or prefix == "00.3_10004141_Граниицы":
        return (
            "ignore",
            True,
            "Оформление, отметки или справочная граница, не физическое препятствие",
        )
    if prefix == "00.1_10004141_Топография" and leaf in TOPO:
        return TOPO[leaf], True, "Явно названный физический класс подосновы"
    if prefix == "00.2_10004141_Сети":
        return (
            "utility",
            True,
            "Сеть учтена; нормативный тип и наружный габарит не подтверждены",
        )
    if prefix == "03_10004141_Проектные решения":
        if leaf.startswith("ДВ_ПП_Тип0_Газон"):
            return (
                "ignore",
                True,
                "Проектное покрытие газона не является существующим деревом; здания и сети проверяются отдельно",
            )
        if leaf.startswith(("ДВ_ПП_", "ДВ_АКР_Лестница", "ДВ_Борт_")) or leaf in {
            "ГП-Д-Разметка",
            "разметка",
            "ДВ_ГП_П_Борт_Пониженный",
            "ДВ_ГП_П_Отмостка_без_борта",
        }:
            return "road", True, "Твёрдое покрытие, ступени, борт или разметка проезда"
        if leaf in PROJECT_STRUCTURES:
            return "building", True, "Проектное сооружение"
        if leaf in PROJECT_RESTRICTED:
            return "restricted", True, "Проектный физический объект"
        if leaf in {"ДВ_ГП_П_Водоотводные_лотки", "ДВ_ГП_П_Воздуховоды"}:
            return (
                "utility",
                True,
                "Коммуникация без подтверждения нормативных параметров",
            )
    if prefix in {"04_10004141_ИОТ1", "ИОТ2"} and any(
        term in leaf
        for term in ("КЛ", "ВЛ", "ПРОВОД", "Труб", "ТРУБ", "Футляр", "футляр")
    ):
        return (
            "utility",
            True,
            "Электросеть или футляр подосновы; параметры остаются неподтверждёнными",
        )
    if prefix == "04_10004141_ИОТ1" and leaf in {
        "ИОТ1_замена светильника",
        "ИОТ1_замена опоры",
    }:
        return "restricted", True, "Опора освещения"
    # Includes mixed lawns/woods, slopes, demolition and unnamed physical layer0.
    # They are NOT silently excluded: the native adapter treats unconfirmed
    # rows as local unknowns even though the old mapper enum only has 'ignore'.
    return "ignore", False, "Назначение неоднозначно; локальная проверка обязательна"


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    binding = LiveBinding.model_validate_json(binding_path(args.database).read_bytes())
    if binding.session.source_sha256 != KUST_DXF_SHA:
        parser.error(
            "These manual decisions belong to the reviewed Kustanayskaya fixture only"
        )
    binding = binding.model_copy(update={"linear_layers": LINEAR_LAYERS})
    save_binding(args.database, binding)
    runtime = create_runtime(args.database)
    try:
        app = runtime.application
        project = app.get(binding.project_id)
        if project.plan and project.plan.objects:
            parser.error("Do not remap a planted project with a demo script")
        records, mappings = [], []
        for layer in project.layers:
            role, confirmed, reason = decision(layer.source_name)
            records.append(
                {
                    "layer": layer.source_name,
                    "role": role,
                    "confirmed": confirmed,
                    "reason": reason,
                }
            )
            mappings.append(
                LayerMapping(
                    layer_id=layer.id,
                    kind=role,
                    confirmed=confirmed,
                    visible=layer.visible,
                )
            )
        app.save_mappings(project.id, mappings)
        app.accept_partial_geometry(project.id, binding.session.snapshot_sha256)
        operation = app.start_geometry_operation(project.id)
        app.run_geometry_operation(operation.id)
        result = app.get_operation(project.id, operation.id)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(
            json.dumps(
                {
                    "project_id": project.id,
                    "source_sha256": KUST_DXF_SHA,
                    "linear_layers": sorted(LINEAR_LAYERS),
                    "decisions": records,
                    "operation": result.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(result.model_dump_json())
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
