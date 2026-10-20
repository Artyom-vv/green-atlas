"""Владение производными файлами во время работы и их привязка к проекту."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from inspect import getattr_static
from pathlib import Path
from typing import Any

_current: ContextVar[tuple[Any, str] | None] = ContextVar(
    "project_resource_owner", default=None
)


@contextmanager
def project_resource_scope(
    repository: Any, project_id: str, *, reserved: bool = False
) -> Iterator[None]:
    """Не удалять файлы живого работника; после него повторить отложенную очистку.

    Репозитории в памяти не имеют файлового lifecycle. Сам scope не читает
    геометрию и не держит транзакцию SQLite во время длительного расчёта.
    """
    # Принимаем только явно подключённого владельца файлов. Динамические
    # __getattr__ у прокси/тестовых портов не предоставляют такое полномочие.
    lifecycle = (
        getattr(repository, "resource_lifecycle", None)
        if getattr_static(repository, "resource_lifecycle", None) is not None
        else None
    )
    if lifecycle is None:
        yield
        return
    with lifecycle.reservation(project_id) if reserved else lifecycle.use(project_id):
        token = _current.set((lifecycle, project_id))
        try:
            yield
        finally:
            _current.reset(token)


def register_project_resource(kind: str, key: str, *, root: Path | None = None) -> None:
    """Записать владение до создания файла, включая повторное использование кеша."""
    owner = _current.get()
    if owner is None:
        return
    lifecycle, project_id = owner
    if root is not None:
        lifecycle.configure_root(kind, root)
    lifecycle.register(project_id, kind, key)
