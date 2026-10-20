"""Явное распределение общего количества посадок по зонам независимо от геометрии и
проверки кандидатов.
"""


def equal_zone_targets(zone_ids: list[str], total: int) -> dict[str, int]:
    """Распределяет остаток в порядке отображения зон проекта без повторов одной зоны."""
    unique = list(dict.fromkeys(zone_ids))
    if not unique:
        return {}
    base, remainder = divmod(total, len(unique))
    return {zone_id: base + (index < remainder) for index, zone_id in enumerate(unique)}


def spread_indices(indices: list[int], count: int) -> list[int]:
    """Выбирает индексы по всей проверенной последовательности точек."""
    if count <= 0:
        return []
    if len(indices) <= count:
        return indices
    if count == 1:
        return [indices[len(indices) // 2]]
    return [
        indices[round(index * (len(indices) - 1) / (count - 1))]
        for index in range(count)
    ]
