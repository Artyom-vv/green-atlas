"""Source-bound read workflows and authoritative completion evidence."""
import re

from app.agent_runtime.contracts import AgentIntent, ReadIntent, ReadOutcome, ReadPageEvidence, ToolCall
from app.agent_runtime.semantic_guardrails import _tokens, _SELECT_VERBS
from app.contracts import SpeciesShortlistItem
from app.agent_runtime.selection import bound_scope, selection_problem


def compile_read_intent(text: str, operation: str, *, zone_labels: list[str], object_ids: list[str], source_turns: list[str] | None = None) -> ReadIntent | None:
    if operation != "inspect":
        return None
    source = "\n\n".join(source_turns or [text])
    tokens = _tokens(source)
    shortlist = any(token in _SELECT_VERBS for token in tokens) and any(token.startswith(("пород", "состав", "вид")) for token in tokens)
    diagnostics = (any(token.startswith("план") for token in tokens)
                   and (any(token.startswith(("провер", "проанализ", "оцени")) for token in tokens)
                        or any(token.startswith(("ошиб", "наруш", "огранич", "замечан")) for token in tokens)))
    if shortlist and diagnostics:
        raise ValueError("Проверка плана и подбор пород — разные результаты. Укажите один для этого задания.")
    if not shortlist and not diagnostics:
        return None
    capability = "species_shortlist" if shortlist else "plan_issues"
    # This narrow vocabulary is the supported query contract, not a general
    # language parser. Qualifiers without a domain argument become questions.
    for label in [*zone_labels, *object_ids]:
        source = re.sub(rf'(?<!\w){re.escape(label)}(?!\w)', ' ', source, flags=re.IGNORECASE)
    allowed_stems = ("провер", "покаж", "показ", "проанализ", "оцени", "найд", "текущ", "сохран", "план",
        "посад", "растен", "дерев", "кустар", "огранич", "наруш", "замечан", "ошиб", "результат",
        "подходящ", "подоб", "подбер", "пород", "вид", "состав", "участ", "зон", "област", "проект", "выбер", "выбран", "выдел", "используй", "весь", "всего")
    words = {"на", "в", "во", "для", "из", "и", "по", "у", "этот", "этого", "этом", "эти", "мне", "пожалуйста", "все", "всех", "всем", "всём"}
    source_tokens = _tokens(source)
    extra = [token for index, token in enumerate(source_tokens) if token not in words
             and not (token.isdigit() and index and source_tokens[index - 1].startswith(("участ", "зон", "област")))
             and not token.startswith(allowed_stems)]
    return ReadIntent(capability=capability, unsupported_requirements=[" ".join(dict.fromkeys(extra))] if extra else [])


def read_arguments(intent: AgentIntent, resolved_scope, *, offset: int = 0) -> dict:
    if intent.read is None:
        raise ValueError("Не определён проверяемый результат чтения")
    if intent.read.unsupported_requirements or intent.unresolved_requirements or intent.hard_constraints or intent.preferences or intent.species_ids:
        raise ValueError("Для дополнительных условий этого запроса пока нет подключённой проверки. Уточните требуемый результат.")
    if intent.goal.target_count is not None or (intent.read.capability == "plan_issues" and intent.plant_kind is not None):
        raise ValueError("Этот сценарий чтения пока не поддерживает выбор по количеству или типу посадок. Укажите конкретную область или посадки.")
    if intent.scope_mode == "project":
        zones, objects = [], []
    elif intent.scope_mode in {"explicit", "selection"}:
        problem = selection_problem(intent)
        if problem:
            raise ValueError(problem.message)
        zones, objects = bound_scope(intent)
    elif resolved_scope is not None:
        zones, objects = list(resolved_scope.zone_ids), list(resolved_scope.object_ids)
    else:
        raise ValueError("Укажите конкретный участок или посадки для этого запроса.")
    if (zones and objects) or (intent.scope_mode != "project" and not zones and not objects):
        raise ValueError("Укажите одну область чтения: участки либо конкретные посадки.")
    if intent.read.capability == "species_shortlist":
        if not zones and not objects:
            raise ValueError("Укажите участок или посадки: подбор пород требует реальной области расчёта.")
        return {"zone_ids": zones, "object_ids": objects,
                "kind": intent.plant_kind if intent.plant_kind in {"tree", "shrub"} else None}
    return {"zone_ids": zones, "object_ids": objects, "offset": offset, "limit": 100}


def verify_read_page(intent: AgentIntent, scope, request: ToolCall, arguments: dict, data, *, project_id: str, run_id: str | None, snapshot_version: int, plan_version: int | None) -> ReadPageEvidence | None:
    if intent.read is None or request.name != intent.read.capability:
        return None
    expected = read_arguments(intent, scope, offset=arguments.get("offset", 0))
    if any(set(arguments.get(key) or []) != set(expected.get(key) or []) for key in ("zone_ids", "object_ids")):
        raise ValueError("Область чтения не соответствует поручению")
    if request.name == "plan_issues":
        if arguments.get("rule_ids") or arguments.get("severity"):
            raise ValueError("Запрос проверки не разрешает скрывать замечания дополнительными фильтрами")
        if not isinstance(data, dict) or data.get("source") != "saved_plan_validation" or data.get("plan_version") != plan_version:
            raise ValueError("Результаты проверки не подтверждают источник и версию плана")
        items = data.get("items")
        if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] for item in items):
            raise ValueError("Страница проверки содержит некорректные записи")
        ids = [item["id"] for item in items]
        total, offset, next_offset = data.get("total"), data.get("offset"), data.get("next_offset")
        if (type(total) is not int or type(offset) is not int or total < 0 or offset != arguments.get("offset", 0)
                or len(items) > arguments.get("limit", 20) or len(ids) != len(set(ids)) or offset + len(ids) > total):
            raise ValueError("Страница проверки не подтверждает число полученных записей")
        end = offset + len(ids)
        if next_offset != (end if end < total else None) or (next_offset is not None and not ids):
            raise ValueError("Последовательность страниц проверки неполна")
        if not data.get("scope_note") or data.get("global_issues_excluded") is not bool(expected["zone_ids"] or expected["object_ids"]):
            raise ValueError("У результата проверки нет оговорки об области и полноте")
        return ReadPageEvidence(project_id=project_id, run_id=run_id, call_id=request.call_id, capability=request.name, source="saved_plan_validation", zone_ids=expected["zone_ids"], object_ids=expected["object_ids"],
            snapshot_version=snapshot_version, plan_version=plan_version, total=total, offset=offset, item_ids=ids,
            next_offset=next_offset, global_issues_excluded=data["global_issues_excluded"], caveat=data["scope_note"])
    if arguments.get("kind") != expected["kind"] or not isinstance(data, list):
        raise ValueError("Состав запроса пород не соответствует поручению")
    items = [SpeciesShortlistItem.model_validate(item) for item in data]
    ids = [item.species.id for item in items]
    if len(ids) != len(set(ids)) or (expected["kind"] and any(item.species.kind != expected["kind"] for item in items)):
        raise ValueError("Подбор содержит повторные или не запрошенные виды растений")
    return ReadPageEvidence(project_id=project_id, run_id=run_id, call_id=request.call_id, capability=request.name, source="species_suitability", zone_ids=expected["zone_ids"], object_ids=expected["object_ids"], kind=expected["kind"],
        snapshot_version=snapshot_version, plan_version=plan_version, total=len(ids), offset=0, item_ids=ids,
        caveat="Подбор по текущим данным участка. Статусы available/review и оценки вместимости не заменяют проверенный preview посадки.")


def read_progress(record, events) -> tuple[ReadOutcome | None, int]:
    """Only contiguous pages from this run epoch and version prove completion."""
    intent = record.state.intent
    if intent.read is None:
        return None, 0
    pages = []
    refs = []
    ids = set()
    offset = 0
    expected = read_arguments(intent, record.state.resolved_scope)
    for event in events:
        payload = event.payload
        if event.kind != "tool_result" or payload.get("name") != intent.read.capability or payload.get("status") != "succeeded" or not payload.get("read_page"):
            continue
        page = ReadPageEvidence.model_validate(payload["read_page"])
        if page.project_id != record.state.project_id or page.run_id != record.state.run_id or page.call_id != payload.get("call_id"):
            raise ValueError("Доказательство чтения относится к другому проекту, заданию или вызову")
        if page.snapshot_version != record.state.snapshot_version:
            continue
        if page.plan_version != record.state.plan_version:
            raise ValueError("Страница относится к другой версии плана")
        versions = payload.get("resource_versions") or {}
        if versions.get("project") != page.snapshot_version or versions.get("plan") != (page.plan_version if page.plan_version is not None else "none"):
            raise ValueError("Версии доказательства чтения не совпадают с результатом инструмента")
        if (page.capability != intent.read.capability or set(page.zone_ids) != set(expected["zone_ids"])
                or set(page.object_ids) != set(expected["object_ids"]) or page.kind != expected.get("kind")):
            raise ValueError("Сохранённая страница относится к другой области чтения")
        if (page.offset != offset or ids.intersection(page.item_ids) or payload.get("call_id") in refs
                or (pages and (page.total != pages[0].total or page.source != pages[0].source or page.caveat != pages[0].caveat))):
            raise ValueError("Сохранённые страницы не образуют полный последовательный результат")
        pages.append(page)
        refs.append(payload["call_id"])
        ids.update(page.item_ids)
        offset += len(page.item_ids)
        if page.next_offset is None:
            if offset != page.total:
                raise ValueError("Число прочитанных записей не совпадает с итогом")
            return ReadOutcome(capability=page.capability, source=page.source, total=page.total, pages=len(pages),
                zone_ids=page.zone_ids, object_ids=page.object_ids, kind=page.kind, snapshot_version=page.snapshot_version,
                plan_version=page.plan_version, evidence_refs=refs, global_issues_excluded=page.global_issues_excluded, caveat=page.caveat), offset
    return None, offset
