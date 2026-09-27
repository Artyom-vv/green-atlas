from copy import deepcopy
from xml.etree import ElementTree as ET

from app.dxf_import.contracts import ImportMode, ImportStatus
from app.geometry.contracts import GeometrySnapshot
from app.geometry.query_adapters import IndexedGeometryQuery
from app.projects.contracts import Project


def test_overview_declares_semantics_without_closing_open_lines():
    from shapely.geometry import LineString, Polygon
    from app.geometry.source_overview import source_overview

    result = source_overview([
        ({'properties': {'source_layer': 'Газон', 'kind': 'lawn'}},
         Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])),
        ({'properties': {'source_layer': 'Здание', 'kind': 'building'}},
         LineString([(12, 0), (12, 10)])),
    ], (-1, -1, 13, 11), 1)
    groups = {g.attrib['data-kind']: g for g in ET.fromstring(result['svg'])}
    assert groups['lawn'].attrib['data-shape'] == 'area'
    assert groups['building'].attrib['data-shape'] == 'line'
    assert groups['building'].attrib['fill'] == 'none'


def test_picking_budget_does_not_erase_unmapped_native_context():
    features = [{'type': 'Feature', 'id': str(i),
        'properties': {'kind': 'building' if i == 0 else 'ignore',
                       'source_layer': 'Survey & "original"'},
        'geometry': {'type': 'LineString', 'coordinates': [[i, 0], [i, 10]]}}
        for i in range(20)]
    original = deepcopy(features)
    project = Project(name='native', import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE),
        source_geometry=GeometrySnapshot(feature_collection={'features': features}))
    response = IndexedGeometryQuery(max_features=1).query(project, (-1, -1, 21, 11), 1)
    collection = response.feature_collection
    assert len(collection['features']) == 1
    assert collection['metadata']['truncated']
    overview = collection['source_overview']
    assert overview['source_features'] == overview['rendered_features'] == 20
    assert overview['complete'] and overview['display_only']
    svg = ET.fromstring(overview['svg'])
    assert {group.attrib['data-source-layer'] for group in svg} == {'Survey & "original"'}
    assert ''.join(path.attrib['d'] for group in svg for path in group).count('M') == 20
    assert features == original
    # Existing full-DXF background has its own display; do not send two plots.
    project.import_status.mode = ImportMode.SOURCE_DXF
    assert 'source_overview' not in IndexedGeometryQuery(max_features=1).query(
        project, (-1, -1, 21, 11), 1).feature_collection


def test_overview_preserves_holes_and_separate_entities_not_derived_constraints():
    from shapely.geometry import LineString, Polygon

    from app.geometry.source_overview import source_overview
    polygon = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)],
                      holes=[[(2, 2), (2, 4), (4, 4), (4, 2)]])
    feature = {'properties': {'source_layer': 'original', 'kind': 'building'}}
    result = source_overview([(feature, polygon), (feature, polygon),
        ({'properties': {'kind': 'forbidden'}}, LineString([(0, 0), (1, 1)]))],
        (-1, -1, 11, 11), 0.1)
    svg = ET.fromstring(result['svg'])
    paths = list(svg[0])
    assert len(paths) == 2
    assert all(path.attrib['d'].count('Z') == 2 for path in paths)
    assert result['rendered_features'] == 2


def test_overview_reuses_paths_but_frames_the_current_viewport(monkeypatch):
    from shapely.geometry import LineString

    import app.geometry.source_overview as module

    original = module._source_drawing
    calls = []

    def observed(candidates, resolution, cache=None):
        calls.append(resolution)
        return original(candidates, resolution, cache)

    monkeypatch.setattr(module, '_source_drawing', observed)
    cache = module.SourceOverviewCache()
    candidates = [({'properties': {'source_layer': 'Сети', 'kind': 'utility'}},
                   LineString([(0, 0), (10, 10)]))]
    first = cache.render(iter(candidates), (-1, -1, 11, 11), 1, b'one')
    second = cache.render(iter(candidates), (-2, -2, 12, 12), 1, b'one')
    assert calls == [1]
    assert second == module.source_overview(iter(candidates), (-2, -2, 12, 12), 1)
    assert first['extent'] != second['extent']
    assert 'viewBox="-2 -12 14 14"' in second['svg']
    cache.render(iter(candidates), (-2, -2, 12, 12), 2, b'one')
    cache.render(iter(candidates), (-2, -2, 12, 12), 2, b'two')
    assert calls == [1, 1, 2, 2]


def test_overview_cache_is_bounded_and_discarded_with_geometry_revision():
    from shapely.geometry import LineString

    import app.geometry.source_overview as module

    candidates = [({'properties': {'source_layer': 'Здание', 'kind': 'building'}},
                   LineString([(0, 0), (10, 10)]))]
    cache = module.SourceOverviewCache(max_entries=1)
    first = cache.render(candidates, (-1, -1, 11, 11), 1, b'first')
    cache.render(candidates, (-1, -1, 11, 11), 1, b'second')
    assert len(cache._drawings) == 1
    assert cache._bytes <= cache.max_bytes
    assert cache.render(candidates, (-1, -1, 11, 11), 1, b'first') == first
    tiny = module.SourceOverviewCache(max_bytes=1)
    assert tiny.render(candidates, (-1, -1, 11, 11), 1, b'first') == first
    assert not tiny._drawings and tiny._bytes == 0

    features = [{'type': 'Feature', 'id': str(i),
                 'properties': {'source_layer': 'Здание', 'kind': 'building'},
                 'geometry': {'type': 'LineString', 'coordinates': [[i, 0], [i, 10]]}}
                for i in range(2)]
    project = Project(name='native', import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE),
                      source_geometry=GeometrySnapshot(feature_collection={'features': features}))
    query = IndexedGeometryQuery(max_features=1)
    initial = query.query(project, (-1, -1, 11, 11), 1)
    old_index = query._indexes[project.id]
    assert old_index.source_overviews._drawings
    project.geometry_version += 1
    features[1]['geometry']['coordinates'][1] = [2, 10]
    changed = query.query(project, (-1, -1, 11, 11), 1)
    assert query._indexes[project.id] is not old_index
    assert initial.feature_collection['source_overview']['svg'] != changed.feature_collection['source_overview']['svg']
    query.discard(project.id)
    assert query.cached_project_count == 0


def test_overlapping_pan_only_prepares_new_paths_and_matches_uncached_output(monkeypatch):
    from shapely.geometry import LineString

    import app.geometry.source_overview as module
    original = module._display_paths
    calls = []

    def observed(geometry, resolution):
        calls.append(id(geometry))
        return original(geometry, resolution)

    monkeypatch.setattr(module, '_display_paths', observed)
    features = [({'properties': {'source_layer': 'Сети', 'kind': 'utility'}},
                 LineString([(i, 0), (i, 10)])) for i in range(3)]
    cache = module.SourceOverviewCache()
    cache.render(features[:2], (-1, -1, 2, 11), 0.2, b'first')
    panned = cache.render(features[1:], (0, -1, 3, 11), 0.2, b'pan')
    assert len(calls) == 3
    assert panned == module.source_overview(features[1:], (0, -1, 3, 11), 0.2)
    assert cache._paths.bytes <= cache._paths.max_bytes
    tiny = module.SourcePathCache(max_bytes=1)
    assert tiny.paths(features[0][1], 0.2) == original(features[0][1], 0.2)
    assert not tiny.entries and tiny.bytes == 0
