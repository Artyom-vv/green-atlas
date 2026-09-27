from types import SimpleNamespace

from app.species.catalog import forecast_at, get_species
from app.releases.service import _forecast


def test_forecast_at_interpolates_arbitrary_year_between_catalogue_anchors() -> None:
    species = get_species("tilia-cordata@2026-08-28.1")

    forecast = forecast_at(species.canopy_forecast, 23)

    assert forecast is not None
    assert forecast.horizon_year == 23
    assert forecast.radius_min_m == 3.423
    assert forecast.radius_max_m == 7.0
    assert forecast.confidence == "low"


def test_forecast_at_does_not_extrapolate_beyond_catalogue_anchors() -> None:
    species = get_species("tilia-cordata@2026-08-28.1")

    assert forecast_at(species.canopy_forecast, -1) is None
    assert forecast_at(species.canopy_forecast, 41) is None


def test_release_forecast_uses_the_same_interpolation_at_arbitrary_year() -> None:
    species = get_species("tilia-cordata@2026-08-28.1")
    object_ = SimpleNamespace(canopy_forecast=species.canopy_forecast)

    assert _forecast(object_, "canopy_forecast", 23) == ("3.423", "7.0")
