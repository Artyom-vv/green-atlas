"""Общий каталог объяснений: локальные тексты и подстановка параметров.

Модуль не зависит от хранилищ или HTTP и доступен проверяющим правилам.
Перевод доменных исключений в публичный отказ принадлежит error_messages.
"""

from collections.abc import Mapping
from importlib.resources import files

from pydantic import TypeAdapter

from app.shared.failures import FailureParams, FailureReason

_MESSAGES = TypeAdapter(dict[str, str]).validate_json(
    files("app.shared").joinpath("messages.ru.json").read_bytes()
)
_PARAMS = TypeAdapter(FailureParams)


def message(
    key: FailureReason | str, params: Mapping[str, object] | None = None
) -> str:
    if key == FailureReason.RELEASE_FINAL_UNAVAILABLE:
        checks = _PARAMS.validate_python(params or {}, strict=True)["release_checks"]
        return _MESSAGES[key].format(
            reasons="; ".join(
                message(f"release.{check['code']}", check) for check in checks
            )
        )
    values = {
        name: ", ".join(value)
        if name == "layer_names" and isinstance(value, list)
        else value
        for name, value in (params or {}).items()
    }
    return _MESSAGES[key].format_map(values)


def has_message(key: str) -> bool:
    return key in _MESSAGES
