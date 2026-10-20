"""Ошибка закрытия одного владельца не пропускает остальные ресурсы runtime."""

from threading import Event, Thread
from unittest.mock import Mock

import pytest

from app import composition

_HAS_RENDERER = "_scene_rendering" in composition.Runtime.__dataclass_fields__
_RESOURCES = (
    ("recognition", "renderer", "operations", "projects")
    if _HAS_RENDERER
    else ("recognition", "operations", "projects")
)


def owned_runtime(
    failures: set[str], *, lazy_owned: bool = True
) -> tuple[composition.Runtime, list[str]]:
    events: list[str] = []

    def resource(name: str) -> Mock:
        def close() -> None:
            events.append(name)
            if name in failures:
                raise RuntimeError("close-" + name)

        return Mock(close=Mock(side_effect=close))

    runtime = composition.Runtime(
        database_path="/not-opened/projects.sqlite3",
        application=Mock(),
        project_repository=resource("projects"),
        operation_repository=resource("operations"),
        cad_assets=Mock(),
    )
    if lazy_owned:
        runtime._layer_recognition = resource("recognition")
        if _HAS_RENDERER:
            setattr(runtime, "_scene_rendering", resource("renderer"))
    return runtime, events


@pytest.mark.parametrize("failure", [None, *_RESOURCES])
def test_every_owned_resource_closes_after_any_one_failure_and_only_once(
    failure: str | None,
) -> None:
    runtime, events = owned_runtime({failure} if failure is not None else set())
    if failure is None:
        runtime.close()
    else:
        with pytest.raises(RuntimeError, match="close-" + failure):
            runtime.close()
    assert events == list(_RESOURCES)
    assert runtime._closed
    runtime.close()
    assert events == list(_RESOURCES)


def test_multiple_cleanup_errors_still_close_every_resource() -> None:
    runtime, events = owned_runtime(set(_RESOURCES))
    with pytest.raises(RuntimeError, match="close-projects"):
        runtime.close()
    assert events == list(_RESOURCES)
    runtime.close()
    assert events == list(_RESOURCES)


def test_close_never_initializes_unowned_lazy_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden = Mock(side_effect=AssertionError("Нельзя создавать сервис для закрытия"))
    monkeypatch.setattr(composition, "configured_provider", forbidden)
    monkeypatch.setattr(composition, "LayerRecognitionService", forbidden)
    if _HAS_RENDERER:
        monkeypatch.setattr(composition, "SceneRendering", forbidden)
    runtime, events = owned_runtime(set(), lazy_owned=False)
    runtime.close()
    assert events == ["operations", "projects"]
    assert runtime._layer_recognition is None
    if _HAS_RENDERER:
        assert getattr(runtime, "_scene_rendering") is None
    forbidden.assert_not_called()


@pytest.mark.parametrize("failure", [None, *_RESOURCES])
def test_process_owner_clears_global_runtime_even_after_close_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    runtime, events = owned_runtime({failure} if failure is not None else set())
    monkeypatch.setattr(composition, "_runtime", runtime)
    if failure is None:
        composition.close_runtime()
    else:
        with pytest.raises(RuntimeError, match="close-" + failure):
            composition.close_runtime()
    assert composition._runtime is None
    assert events == list(_RESOURCES)
    composition.close_runtime()
    assert events == list(_RESOURCES)


@pytest.mark.parametrize("failure", [None, "maintenance", "drain"])
def test_maintenance_stops_outside_runtime_lock_and_other_owners_always_close(
    failure: str | None,
) -> None:
    runtime, events = owned_runtime(set())
    observed = Event()

    def inspect_runtime() -> None:
        with runtime._resource_lock:
            assert runtime._closed
            observed.set()

    def stop_maintenance() -> None:
        events.append("maintenance")
        idle = Thread(target=inspect_runtime, daemon=True)
        idle.start()
        assert observed.wait(2), "Maintenance must not wait on the runtime lock"
        idle.join(timeout=2)
        assert not idle.is_alive()
        if failure == "maintenance":
            raise RuntimeError("close-maintenance")

    def drain() -> None:
        events.append("drain")
        if failure == "drain":
            raise RuntimeError("close-drain")

    runtime._storage_worker = Mock(close=Mock(side_effect=stop_maintenance))
    runtime.project_repository.resource_lifecycle.drain.side_effect = drain
    if failure is None:
        runtime.close()
    else:
        with pytest.raises(RuntimeError, match="close-" + failure):
            runtime.close()
    expected = [
        "maintenance",
        "recognition",
        "renderer",
        "drain",
        "operations",
        "projects",
    ]
    assert events == expected
    runtime.close()
    assert events == expected
