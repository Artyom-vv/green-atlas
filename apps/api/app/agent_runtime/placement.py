"""Deterministic capacity facts at the placement capability boundary."""

from app.agent_runtime.contracts import PlacementOutcome, PlacementRemedy


def placement_outcome(proposal: dict, *, requested: int | None) -> PlacementOutcome:
    change_set = proposal.get("change_set") or {}
    additions = change_set.get("additions") or []
    # A rejected change-set has no positions that can be offered as a whole.
    found = len(additions) if change_set.get("can_apply") is True else 0
    shortfall = max(0, requested - found) if requested is not None else 0
    status = "impossible" if not found else "partial" if shortfall else "exact"
    reason = None
    remedies = []
    if status != "exact":
        reason = proposal.get("shortfall_explanation") or ((
            f"В проверенных вариантах найдено {found} из {requested} мест. "
            if requested is not None else "В проверенных вариантах не найдено допустимых мест. "
        ) + "Участок и условия сохранены; максимальная вместимость не установлена.")
        if found:
            remedies.append(PlacementRemedy(
                code="reduce_quantity", label=f"Снизить количество до {found}", target_count=found,
            ))
        remedies.extend([
            PlacementRemedy(code="change_scope", label="Выбрать другой участок"),
            PlacementRemedy(code="change_arrangement", label="Изменить схему размещения"),
            PlacementRemedy(code="change_species", label="Рассмотреть другие породы"),
        ])
    return PlacementOutcome(
        status=status, requested=requested, found=found, shortfall=shortfall,
        reason=reason, remedy_options=remedies,
    )
