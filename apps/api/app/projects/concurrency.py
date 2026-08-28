from __future__ import annotations

from contextvars import ContextVar, Token


_expected_project_version: ContextVar[int | None] = ContextVar("expected_project_version", default=None)


class ProjectVersionConflict(RuntimeError):
    def __init__(self, project_id: str, expected_version: int, current_version: int) -> None:
        self.project_id = project_id
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__("Проект изменён в другой вкладке. Обновите данные перед повтором действия.")


def parse_if_match(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:].strip()
    normalized = normalized.strip('"')
    if normalized.startswith("v"):
        normalized = normalized[1:]
    try:
        version = int(normalized)
    except ValueError as error:
        raise ValueError('If-Match должен содержать версию проекта, например "12"') from error
    if version < 1:
        raise ValueError("Версия проекта должна быть положительным числом")
    return version


def set_expected_project_version(value: str | None) -> Token[int | None]:
    return _expected_project_version.set(parse_if_match(value))


def reset_expected_project_version(token: Token[int | None]) -> None:
    _expected_project_version.reset(token)


def expected_project_version() -> int | None:
    return _expected_project_version.get()


def advance_expected_project_version(version: int) -> None:
    if _expected_project_version.get() is not None:
        _expected_project_version.set(version)


def assert_project_version(project_id: str, current_version: int) -> None:
    expected = expected_project_version()
    if expected is not None and expected != current_version:
        raise ProjectVersionConflict(project_id, expected, current_version)
