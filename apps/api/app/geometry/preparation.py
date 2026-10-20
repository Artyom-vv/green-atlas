"""Необязательная предварительная подготовка общей геометрии, привязанная к снимку и
доступная всем сценариям.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from threading import local
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from app.geometry.ports import GeometryCalculationScopePort, GeometryEnginePort
    from app.projects.contracts import Project


_scopes = local()


def calculation_scope(
    geometry: GeometryEnginePort, project: Project
) -> AbstractContextManager[None]:
    """Проверять ресурсы всей порции, включая заранее отклонённые кандидаты."""
    if getattr(type(geometry), "calculation_scope", None) is None:
        return nullcontext()
    return _shared_calculation_scope(geometry, project)


@contextmanager
def _shared_calculation_scope(
    geometry: GeometryEnginePort, project: Project
) -> Iterator[None]:
    # Ресурсы провайдеров привязаны к потоку. Повторный вход того же расчёта
    # использует внешнюю область; другой проект или ревизия получают свою.
    key = (id(geometry), project.id, project.state_version, project.geometry_version)
    active = getattr(_scopes, "active", set())
    if key in active:
        yield
        return
    with cast("GeometryCalculationScopePort", geometry).calculation_scope(project):
        active.add(key)
        _scopes.active = active
        try:
            yield
        finally:
            active.remove(key)
            if not active:
                del _scopes.active


def prepare_positions(
    geometry: GeometryEnginePort,
    project: Project,
    points: list[tuple[float, float]],
) -> None:
    """Однократно подготовить исходные измерения без кеширования или подавления ошибок."""
    prepare = getattr(geometry, "prepare_positions", None)
    if prepare is not None:
        prepare(project, points)
