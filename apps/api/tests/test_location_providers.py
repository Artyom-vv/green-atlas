import json
import math
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from shapely.geometry import LineString, mapping, shape

from app.location import providers as p


def suggestion(**changes):
    data = {
        "city": "Москва",
        "city_fias_id": "city-id",
        "street": "Мира",
        "street_type_full": "улица",
        "street_with_type": "ул Мира",
        "street_fias_id": "street-id",
        "fias_id": "street-id",
        "geo_lon": "37.6",
        "geo_lat": "55.75",
        "qc_geo": "2",
        **changes,
    }
    return {
        "value": "г Москва, ул Мира",
        "unrestricted_value": "г Москва, ул Мира",
        "data": data,
    }


def address(**changes):
    return p._suggestion(suggestion(**changes), "street")


def street(identifier=1, points=None, nodes=None, name="улица Мира"):
    points = points or [(37.6, 55.75), (37.601, 55.75)]
    return {
        "type": "way",
        "id": identifier,
        "nodes": nodes or [identifier * 2, identifier * 2 + 1],
        "geometry": [{"lon": x, "lat": y} for x, y in points],
        "version": 3,
        "timestamp": "2026-10-10T12:00:00Z",
        "tags": {"name": name, "highway": "residential"},
    }


def osm_payload(*ways):
    return {
        "elements": list(ways),
        "osm3s": {"timestamp_osm_base": "2026-10-13T12:00:00Z"},
    }


def http_client(payload, *, status=200, capture=None):
    def handler(request):
        if capture is not None:
            capture.append(request)
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_suggest_and_selection_use_suggest_token_only_and_preserve_identity():
    requests = []
    with http_client({"suggestions": [suggestion()]}, capture=requests) as client:
        rows = p.suggest_addresses(
            "Мир", city_fias_id="city-id", token="test-token", client=client
        )
        selected = p.select_address(
            rows[0]["unrestricted_value"],
            kind="street",
            expected_fias_id=rows[0]["id"],
            city_fias_id="city-id",
            token="test-token",
            client=client,
        )
    assert [json.loads(request.content)["count"] for request in requests] == [10, 1]
    assert json.loads(requests[1].content)["query"] == "г Москва, ул Мира"
    assert json.loads(requests[0].content)["locations"] == [
        {"country_iso_code": "RU", "city_fias_id": "city-id"}
    ]
    assert all(request.url == p.DADATA_ENDPOINT for request in requests)
    assert all(
        request.headers["authorization"] == "Token test-token" for request in requests
    )
    assert all("x-secret" not in request.headers for request in requests)
    assert selected["street_fias_id"] == selected["id"] == "street-id"
    assert selected["longitude"] == 37.6 and selected["qc_geo"] == 2
    assert "test-token" not in json.dumps(selected)


def test_selection_rejects_changed_identity_and_city():
    for data in ({"street_fias_id": "other"}, {"city_fias_id": "other"}):
        with http_client({"suggestions": [suggestion(**data)]}) as client:
            with pytest.raises(p.LocationProviderError) as error:
                p.select_address(
                    "г Москва, ул Мира",
                    kind="street",
                    expected_fias_id="street-id",
                    city_fias_id="city-id",
                    token="token",
                    client=client,
                )
        assert error.value.code == "address_mismatch"


def test_unconfigured_does_not_request_network(monkeypatch, tmp_path):
    monkeypatch.delenv("DADATA_API_KEY", raising=False)
    monkeypatch.setenv("GREEN_ATLAS_LOCATION_SETTINGS", str(tmp_path / "missing.json"))
    assert p.load_dadata_token() is None
    with pytest.raises(p.LocationProviderError) as error:
        p.suggest_addresses("Москва", kind="city")
    assert error.value.code == "not_configured"


def test_single_address_search_does_not_require_a_separate_city_selection():
    requests = []
    metro = suggestion(street_fias_id=None, street=None, fias_id="metro-id")
    def respond(request):
        requests.append(request)
        rows = [suggestion()] if json.loads(request.content)["count"] == 1 else [metro, suggestion()]
        return httpx.Response(200, json={"suggestions": rows})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        rows = p.suggest_addresses("Москва Мира", token="token", client=client)
        selected = p.select_address(
            rows[0]["unrestricted_value"], kind="street", expected_fias_id="street-id",
            token="token", client=client,
        )
    assert len(rows) == 1
    assert rows[0]["street_fias_id"] == selected["street_fias_id"] == "street-id"
    assert json.loads(requests[0].content)["locations"] == [{"country_iso_code": "RU"}]
    assert json.loads(requests[0].content)["query"] == "Москва Мира"
    assert json.loads(requests[1].content)["count"] == 1


def test_settings_priority_and_private_permissions(monkeypatch, tmp_path):
    settings = tmp_path / "dadata.json"
    settings.write_text('{"api_key":"file-token"}')
    settings.chmod(0o600)
    monkeypatch.delenv("DADATA_API_KEY", raising=False)
    monkeypatch.setenv("GREEN_ATLAS_LOCATION_SETTINGS", str(settings))
    assert p.load_dadata_token() == "file-token"
    monkeypatch.setenv("DADATA_API_KEY", "env-token")
    assert p.load_dadata_token() == "file-token"
    settings.unlink()
    assert p.load_dadata_token() == "env-token"
    settings.write_text("invalid")
    with pytest.raises(p.LocationProviderError) as error:
        p.load_dadata_token()
    assert error.value.code == "settings_unreadable"
    monkeypatch.delenv("DADATA_API_KEY")
    if os.name != "nt":
        settings.write_text('{"api_key":"file-token"}')
        settings.chmod(0o644)
        with pytest.raises(p.LocationProviderError, match="права"):
            p.load_dadata_token()


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (403, "provider_auth"),
        (429, "provider_rate_limit"),
        (502, "provider_unavailable"),
    ],
)
def test_provider_http_errors_are_not_empty_success(status, code):
    with http_client({"secret": "private-server-body"}, status=status) as client:
        with pytest.raises(p.LocationProviderError) as error:
            p.suggest_addresses(
                "Москва", kind="city", token="secret-token", client=client
            )
    assert error.value.code == code
    assert "secret-token" not in str(error.value)
    assert "private-server-body" not in str(error.value)


def test_timeout_and_response_limits(tmp_path, monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("private-token", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(p.LocationProviderError) as error:
            p.search_streets(address(), tmp_path, client=client)
    assert error.value.code == "provider_unavailable"
    assert "private-token" not in str(error.value)
    monkeypatch.setattr(p, "MAX_RESPONSE_BYTES", 10)
    with http_client(osm_payload(street())) as client:
        with pytest.raises(p.LocationProviderError) as error:
            p.search_streets(address(), tmp_path, client=client)
    assert error.value.code == "response_limit" and not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("qc_geo", [None, "3", "4", "5"])
def test_city_or_unknown_precision_requires_manual_point(qc_geo, tmp_path):
    with pytest.raises(p.LocationProviderError) as error:
        p.search_streets(address(qc_geo=qc_geo), tmp_path)
    assert error.value.code == "manual_location_required"
    with http_client(osm_payload(street())) as client:
        result = p.search_streets(
            address(qc_geo=qc_geo), tmp_path, near_point=(37.6, 55.75), client=client
        )
    assert result["provenance"]["center_source"] == "manual_map"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("ул. Мира", "Мира улица"),
        ("пр-т Ёлочный", "Елочный проспект"),
        ("б-р Победы", "Победы бульвар"),
        ("1-й проезд", "проезд 1-й"),
    ],
)
def test_street_type_order_and_yo_are_normalized(first, second):
    assert p.normalize_street_name(first) == p.normalize_street_name(second)


def test_connected_segments_combine_but_disconnected_ones_require_selection(tmp_path):
    ways = [
        street(1, nodes=[1, 2]),
        street(2, [(37.601, 55.75), (37.602, 55.75)], [2, 3], "Мира улица"),
        street(3, [(37.61, 55.75), (37.611, 55.75)], [4, 5]),
        street(4, name="проспект Мира"),
        street(5, name="улица Мира Новая"),
    ]
    requests = []
    with http_client(osm_payload(*ways), capture=requests) as client:
        result = p.search_streets(address(), tmp_path, client=client)
    assert len(result["candidates"]) == 2
    assert {tuple(row["osm_ids"]) for row in result["candidates"]} == {
        ("way/1", "way/2"),
        ("way/3",),
    }
    assert all(
        "disconnected_candidates" in row["warnings"] for row in result["candidates"]
    )
    assert all("selected" not in row for row in result["candidates"])
    query = str(requests[0].content)
    assert requests[0].headers["user-agent"] == "GreenAtlas-local-location-evidence/1"
    assert "around" in query and "5000" in query
    assert "out+meta+geom" in query
    assert (
        result["candidates"][0]["provenance"]["osm_timestamp"] == "2026-10-13T12:00:00Z"
    )


def test_crossing_without_shared_node_does_not_connect():
    ways = [
        street(1, [(37.6, 55.75), (37.602, 55.752)], [1, 2]),
        street(2, [(37.6, 55.752), (37.602, 55.75)], [3, 4]),
    ]
    assert len(p._street_candidates(osm_payload(*ways), address())) == 2


def test_malformed_objects_and_inconsistent_node_coordinates_fail_explicitly():
    with pytest.raises(p.LocationProviderError) as error:
        p._street_candidates(osm_payload({"type": "way", "id": 1}), address())
    assert error.value.code == "invalid_response"
    with pytest.raises(p.LocationProviderError) as error:
        p._street_candidates(
            osm_payload(
                street(1, nodes=[1, 2]),
                street(2, [(37.602, 55.75), (37.603, 55.75)], [2, 3]),
            ),
            address(),
        )
    assert error.value.code == "incomplete_response"


def test_dadata_error_payload_is_not_an_empty_suggestion_success():
    with http_client({"error": "unavailable", "suggestions": []}) as client:
        with pytest.raises(p.LocationProviderError) as error:
            p.suggest_addresses("Москва", kind="city", token="token", client=client)
    assert error.value.code == "invalid_response"


def test_partial_osm_response_or_missing_geometry_is_failure(tmp_path, monkeypatch):
    broken = street()
    broken["geometry"].pop()
    for payload in (
        {**osm_payload(street()), "remark": "timeout"},
        osm_payload(broken),
    ):
        with http_client(payload) as client:
            with pytest.raises(p.LocationProviderError) as error:
                p.search_streets(address(), tmp_path, client=client)
        assert error.value.code == "incomplete_response"
    monkeypatch.setattr(p, "MAX_ELEMENTS", 1)
    with http_client(osm_payload(street(1), street(2))) as client:
        with pytest.raises(p.LocationProviderError) as error:
            p.search_streets(address(), tmp_path, client=client)
    assert error.value.code == "response_limit"
    assert not list(tmp_path.glob("*.json"))


def test_cache_is_checked_atomic_and_expires_after_seven_days(tmp_path):
    requests = []
    with http_client(osm_payload(street()), capture=requests) as client:
        first = p.search_streets(address(), tmp_path, client=client)
        second = p.search_streets(address(), tmp_path, client=client)
        assert len(requests) == 1 and not first["cache_hit"] and second["cache_hit"]
        path = next(tmp_path.glob("streets-*.json"))
        entry = json.loads(path.read_text())
        entry["response"] = json.dumps(osm_payload(street(99)))
        path.write_text(json.dumps(entry))
        assert not p.search_streets(address(), tmp_path, client=client)["cache_hit"]
        entry = json.loads(path.read_text())
        entry["provenance"]["fetched_at"] = (
            datetime.now(UTC) - timedelta(days=8)
        ).isoformat()
        path.write_text(json.dumps(entry))
        assert not p.search_streets(address(), tmp_path, client=client)["cache_hit"]
    assert len(requests) == 3 and not list(tmp_path.glob("*.pending"))


def test_cache_entry_count_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "MAX_CACHE_ENTRIES", 2)
    with http_client(osm_payload(street())) as client:
        for delta in range(4):
            p.search_streets(
                address(),
                tmp_path,
                near_point=(37.6 + delta / 100, 55.75),
                client=client,
            )
    assert len(list(tmp_path.glob("streets-*.json"))) == 2


def candidate(points):
    return {
        "id": "selected",
        "geometry": mapping(LineString(points)),
        "osm_ids": ["way/1"],
    }


def test_corridor_uses_metres_at_high_latitude_and_valid_building_bbox():
    source = candidate([(37.6, 60), (37.61, 60)])
    result = p.build_street_corridor(source, buffer_m=100)
    west, south, east, north = result["bbox"]
    assert (north - south) * math.pi / 180 * p.EARTH_RADIUS_M == pytest.approx(200)
    assert (37.6 - west) * math.pi / 180 * p.EARTH_RADIUS_M * math.cos(
        math.radians(60)
    ) == pytest.approx(100)
    assert shape(result["corridor"]).contains(shape(source["geometry"]))
    assert result["bbox_area_m2"] < 4_000_000
    from app.rendering.osm import _query

    assert "building" in _query(tuple(result["bbox"]))


def test_long_street_requires_explicit_map_section():
    source = candidate([(37.6, 55.75), (37.8, 55.75)])
    with pytest.raises(p.LocationProviderError) as error:
        p.build_street_corridor(source)
    assert error.value.code == "area_too_large"
    result = p.build_street_corridor(
        source, section_bbox=[37.605, 55.74, 37.615, 55.76]
    )
    assert result["bbox"][2] < 37.62
    assert result["provenance"]["scope"] == "explicit_map_section"
    assert result["provenance"]["osm_ids"] == ["way/1"]
    assert result["provenance"]["section_bbox"] == [37.605, 55.74, 37.615, 55.76]
    with pytest.raises(p.LocationProviderError) as error:
        p.build_street_corridor(source, section_bbox=[38, 55.74, 38.01, 55.76])
    assert error.value.code == "empty_section"


def test_bounding_rectangle_area_cap_matters_even_for_thin_diagonal_corridor():
    source = candidate([(37.6, 55.75), (37.65, 55.78)])
    with pytest.raises(p.LocationProviderError) as error:
        p.build_street_corridor(source, buffer_m=20)
    assert error.value.code == "area_too_large"
