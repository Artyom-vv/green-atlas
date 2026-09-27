"""Separate one-message limits from the complete, untruncated source history."""

MAX_INITIAL_MESSAGE_CHARS = 2000
MAX_ANSWER_MESSAGE_CHARS = 800
MAX_HISTORY_CHARS = 16000
MAX_HISTORY_TURNS = 128
SOURCE_TURN_SEPARATOR = "\n\n"


class HistoryBudgetExceeded(ValueError):
    pass


def join_source_history(source_turns: list[str]) -> str:
    text = SOURCE_TURN_SEPARATOR.join(source_turns)
    if len(source_turns) > MAX_HISTORY_TURNS or len(text) > MAX_HISTORY_CHARS:
        raise HistoryBudgetExceeded(
            "Достигнут предел истории этого задания. Ответ не добавлен, прежние условия сохранены. "
            "Создайте новое полное поручение с нужными условиями.")
    return text
