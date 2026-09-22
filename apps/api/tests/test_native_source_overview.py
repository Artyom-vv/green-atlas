from copy import deepcopy
from xml.etree import ElementTree as ET

from app.dxf_import.contracts import ImportMode, ImportStatus
from app.geometry.contracts import GeometrySnapshot
from app.geometry.query_adapters import IndexedGeometryQuery
from app.projects.contracts import Project


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
