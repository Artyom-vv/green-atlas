"""Publish compact, reviewable audit evidence; never publish a service project."""

from collections import Counter
import csv
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]


def gate_label(record):
    status = record["status"]
    if status == "assembly_failed":
        message = record.get("error_tail", "")
        if "время" in record.get("process", {}).get("error", ""):
            return "сборка превысила 300 с"
        if "Mixed units" in message:
            return "сборщик останавливается на разных INSUNITS"
        if "entity-count mismatch" in message:
            return "SDK bind не переносит все сущности"
        return "сборка не прошла (см. receipt)"
    if status == "readable_mapping_requires_review":
        if not record.get("calculation"):
            return "reader прошёл; расчёт не завершён"
        if (record.get("calculation") or {}).get("status") == "completed":
            return "reader и запуск расчёта прошли; mapping не проверен"
        return "reader прошёл; расчёт отказал"
    return {
        "pending_inventory": "проверка ещё не завершена",
        "reference_review_or_read_failure": "нужно сопоставить пути XREF; файлы-кандидаты перечислены отдельно",
        "ordinary_upload_limit": "DXF подготовлен; обычный upload не допускает объём >50 МиБ",
        "reader_failed": "reader отказал (см. причину)",
        "reader_process_failed": "ограниченный процесс reader не прошёл",
    }.get(status, status)


def main(directory: Path):
    summary = json.loads((directory / "summary.json").read_text(encoding="utf8"))
    qualification = json.loads((directory / "group-qualification.json").read_text(encoding="utf8"))
    output = ROOT / "docs/implementation/2026-09-16-twenty-street-audit"
    output.mkdir(exist_ok=True)
    table = ["| Улица | CAD-версий внутри улицы: прочитано / всего | Проверки выбранных самостоятельных групп |",
             "| --- | ---: | --- |"]
    streets = []
    file_rows = []
    references = []
    selected_groups = []
    unique = {}
    for street in summary["streets"]:
        nodes = {n["sha256"]: n for n in street["files"]}
        readable = sum(n["status"] == "readable" for n in nodes.values())
        checks = [q for q in qualification if q["street"] == street["street"]]
        labels = sorted(set(gate_label(q) for q in checks))
        if street["street"].startswith("3. "):
            labels.append("успешный расчёт относится только к отдельной дендрологической подложке")
        if street["street"].startswith("9. "):
            labels.append("дополнительно ИП: 166 483 features прочитаны при диагностическом лимите 200 000; mapping ошибочен")
        if not nodes:
            labels = ["CAD не предоставлен в этой папке / основном ZIP: PDF и таблицы"]
        elif not labels:
            labels = ["инвентаризация выполнена; отдельный сквозной запуск не выполнен"]
        table.append(f"| {street['street']} | {readable} / {len(nodes)} | {'; '.join(labels)} |")
        streets.append({"street": street["street"], "cad_paths": len(street["files"]),
                        "unique_contents_within_street": len(nodes), "sdk_readable_contents": readable,
                        "selected_group_gates": labels,
                        "active_reference_counts": dict(Counter(e["resolution"] for e in street["xrefs"] if e["active"])),
                        "unresolved_reference_review_classes": dict(Counter(e.get("review_class") for e in street["xrefs"] if e["active"] and not e["target"])),
                        "declared_packages": street["declared_packages"]})
        references.extend({"street": street["street"], **e} for e in street["xrefs"])
        for node in street["files"]:
            unique[node["sha256"]] = node
            inventory = node.get("inventory", {})
            file_rows.append({"street": street["street"], "path": node["path"], "sha256": node["sha256"],
                              "bytes": node["bytes"], "signature": node["signature"],
                              "sdk_status": node["status"], "source_role_hint": node["role_hint"],
                              "archive": node.get("archive", ""), "archive_sha256": node.get("archive_sha256", ""),
                              "dxf_bytes": node.get("dxf_bytes", ""), "units_code": inventory.get("units", ""),
                              "model_entities": inventory.get("model_entities", ""),
                              "reachable_definition_types": json.dumps(inventory.get("reachable_definition_types", {}), ensure_ascii=False),
                              "active_xrefs": sum(x["active"] for x in inventory.get("xrefs", [])),
                              "memory_budget_mib": node.get("inspection_memory_budget_mib", ""),
                              "converter_output": Path(node.get("dxf") or "").name})
        for check in checks:
            root = next(r for r in street["root_checks"] if r["root"] == check["root"])
            selected_groups.append({"street": street["street"], "root": check["root"],
                                    "files": root["files"], "status": check["status"],
                                    "gate": gate_label(check), "raw_definition_types": root["raw_definition_types_in_files"],
                                    "qualification": check})

    for name, value in (("streets.json", streets), ("references.json", references), ("groups.json", selected_groups)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")
    with (output / "files.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(file_rows[0]))
        writer.writeheader()
        writer.writerows(file_rows)
    receipts = []
    for sha, node in unique.items():
        receipt_path = node.get("receipt")
        if not receipt_path:
            continue
        receipt = json.loads(Path(receipt_path).read_text(encoding="utf8"))
        receipts.append({"sha256": sha, "source": receipt["source"]["path"],
                         "status": receipt["status"], "fidelity_verified": False,
                         "attempts": receipt["attempts"], "inspection_process": receipt.get("inspection_process"),
                         "dxf_sha256": receipt.get("dxf_sha256"), "dxf_bytes": receipt.get("dxf_bytes"),
                         "source_unchanged": receipt["source_unchanged"], "error": receipt.get("error")})
    (output / "conversion-receipts.json").write_text(json.dumps(receipts, ensure_ascii=False, indent=2), encoding="utf8")
    for name in ("source-integrity.json", "mapping-reproduction.json", "frunze-archive-check.json", "tools.json", "peschany-insert-transforms.json", "extra-group-roots.json", "sdk-read-statistics.json"):
        if (directory / name).exists():
            shutil.copyfile(directory / name, output / name)
    if (directory / "retry-4g/tools.json").exists():
        shutil.copyfile(directory / "retry-4g/tools.json", output / "tools-retry-4g.json")
    shutil.copyfile(directory / "manifest.json", output / "source-manifest.json")
    for name in ("runtime-check.json", "audit-consistency.json"):
        if (directory / name).exists():
            shutil.copyfile(directory / name, output / name)
    counts = Counter(n["status"] for n in unique.values())
    index = ["# Проверка 20 улиц: таблица и машинные receipts", "",
             f"Уникальных CAD-содержимых: {len(unique)}; статусы SDK: `{dict(counts)}`.", "",
             "Одна строка — отдельная улица. CAD-версии включают архивы; одинаковые байты",
             "проверены один раз. Сумма по улицам может превышать глобальное число, если",
             "одинаковый файл принадлежит нескольким улицам. Группы и их зависимости",
             "перечислены явно в `groups.json`; общая сборка всех файлов улицы не создавалась.", "",
             *table, "", "## Что означает проверка", "",
             "`SDK-readable` — преобразование в производный DXF и чтение ezdxf. Это не",
             "независимый CAD-эталон, не гарантия отсутствия потерь конвертера и не приёмка.",
             "В каждом выбранном комплекте сохраняются отдельный корень, список файлов,",
             "результат сборки и первая конкретная преграда существующего пути импорта.",
             "Если сборка уже превышает upload 50 МиБ, штатный импорт отклонит её до reader;",
             "не запускать это как якобы прошедший проект. Дополнительный диагностический",
             "проход Измайловской с лимитом 200 000 описан в основном отчёте.", "",
             "**Ни один результат здесь не объявлен полным проверенным editable → расчёт → DXF.**",
             "Назначения слоёв, нормативные расстояния, истинность DWG→DXF, экспорт и FPS",
             "нуждаются в своих проверках; наличие REGION само по себе не означает битый DWG.", "",
             "## Файлы доказательств", "",
             "- [Основной разбор и выводы](../2026-09-16-twenty-street-audit.md)",
             "- [20 улиц](streets.json)", "- [Все CAD-пути и версии](files.csv)",
             "- [Отдельные выбранные комплекты и стадии проверки](groups.json)",
             "- [Ссылки, точные совпадения и кандидаты в пределах улицы](references.json)",
             "- [903 пофайловых receipt](conversion-receipts.json)",
             "- [Неизменность источников по SHA256](source-integrity.json)", ""]
    (output / "README.md").write_text("\n".join(index), encoding="utf8")
    print(json.dumps({"unique": len(unique), "counts": counts, "selected_groups": len(selected_groups),
                      "group_statuses": Counter(g["status"] for g in selected_groups)}, ensure_ascii=False))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
