"""Render growth evidence without letting the model invent numeric ranges."""


def growth_answer(events: list[dict], evidence: list[int]) -> str | None:
    selected = [events[index] for index in evidence]
    if not selected or any(event.get("tool") != "growth_objects" for event in selected):
        return None
    data = [event.get("data", {}).get("result") for event in selected]
    if any(not isinstance(item, dict) or "items" not in item for item in data):
        return None
    horizons = {item["horizon_year"] for item in data}
    if len(horizons) != 1:
        return None  # Comparing different horizons needs a separate layout.
    objects = {obj["object_id"]: obj for item in data for obj in item["items"]}
    horizon = next(iter(horizons))
    lines = [f"Горизонт прогноза: {horizon} лет. Посадок в ответе: {len(objects)}."]
    # Do not call an aggregate range the size of each individual plant.
    for key, label in (("canopy", "Крона"), ("roots", "Корневая зона")):
        values = [obj[key] for obj in objects.values() if obj.get(key) is not None]
        if values:
            low = min(item["diameter_min_m"] for item in values)
            high = max(item["diameter_max_m"] for item in values)
            number = lambda value: f"{value:g}".replace(".", ",")
            lines.append(f"{label}: диапазон диаметров среди этих посадок {number(low)}–{number(high)} м.")
        missing = len(objects) - len(values)
        if missing:
            lines.append(f"{label}: нет прогноза для {missing} посадок.")
        if any(value.get("confidence") == "low" for value in values):
            lines.append(f"{label}: низкая уверенность прогноза.")
    if any(item["total"] > len(objects) for item in data):
        lines.append("Прочитана только часть посадок; диапазоны не описывают весь набор.")
    lines.append("Прогнозные размеры не являются нормативными отступами.")
    return "\n".join(lines)
