"""Domain outcomes that require an explicit target choice before planning."""


class ExistingTargetSelectionRequired(ValueError):
    def __init__(self, requested_count: int | None, found_count: int):
        self.requested_count = requested_count
        self.found_count = found_count
        message = (f"Найдено посадок: {found_count}. Выберите конкретные объекты; в задании указано: {requested_count}."
                   if requested_count is not None else "Подходящие посадки не найдены. Уточните область или состав посадок.")
        super().__init__(message)
