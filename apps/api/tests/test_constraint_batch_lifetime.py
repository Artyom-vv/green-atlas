"""Необрезанная геометрия кеша не должна переживать обработку порции."""

import weakref
from unittest.mock import patch

import pytest
from test_hybrid_search import populate
from test_native_live_provider import rectangle
from test_native_live_provider import setup as native_setup

from app.native_query.search.hybrid_domain import CONFIG

setup = native_setup


@pytest.mark.parametrize("unknown", [False, True])
def test_restored_uncut_geometry_is_released_before_next_batch(setup, unknown):
    engine, _, project = setup
    populate(project)
    if unknown:
        project.layers[1].mapping_confirmed = False
    config = CONFIG.model_copy(update={"batch_objects": 1, "yield_seconds": 10})
    with patch("app.native_query.search.hybrid_domain.CONFIG", config):
        engine.automatic_safe_geometry(project, rectangle(-12, -4, 40, 4), 0.5, "tree")
        search = engine._hybrid
        get = search.constraint_unions.get
        previous = []
        restored = 0

        def checked_get(*args):
            nonlocal restored
            assert all(reference() is None for reference in previous)
            value = get(*args)
            assert value is not None
            previous[:] = [weakref.ref(value.blocked)] + [
                weakref.ref(geometry) for geometry in value.reviews.values()
            ]
            restored += 1
            return value

        with patch.object(search.constraint_unions, "get", checked_get):
            result = engine.automatic_safe_geometry(
                project, rectangle(-11, -3, 39, 3), 0.5, "tree"
            )
        assert restored == 3
        assert result["ga_search_domain"]["cache_hits"] == 3
        assert all(reference() is None for reference in previous)
