"""Единственный владелец нового импорта передаёт геометрию редактору без копии."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest
from test_cad_bridge_compiler import near_closed_building_proposal_probe

from app.composition import create_runtime
from app.dxf_import.contracts import ImportEditability, ImportStatus
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.dxf_import.source import editor_source
from app.geometry.contracts import GeometrySnapshot
from app.geometry.networks.utility_contracts import UtilityContext
from app.operations.progress import OperationCancelled
from app.projects.contracts import Project


def source_project():
    features = [
        {
            "type": "Feature",
            "id": f"cad-{index}",
            "properties": {
                "kind": "ignore",
                "source_layer": name,
                "source_context_only": context_only,
                "source_handle": f"A{index}",
                "source_instance_chain": ["BLOCK", f"NESTED{index}"],
                "utility_context": {"stale": True},
                "custom": {"numeric": [-0.0, 2**100], "caption": "Кириллица"},
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                    [[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]],
                ],
            },
        }
        for index, (name, context_only) in enumerate(
            [("Дорога", False), ("Водопровод", False), ("Водопровод", True)]
        )
    ]
    layers = [
        Layer(
            id=name,
            source_name=name,
            suggested_kind=kind,
            mapped_kind=kind,
            object_count=1,
            color="#123456",
            utility_context=UtilityContext() if kind == LayerKind.UTILITY else None,
        )
        for name, kind in [
            ("Дорога", LayerKind.ROAD),
            ("Водопровод", LayerKind.UTILITY),
        ]
    ]
    return Project(
        name="Исходник с внешним владельцем",
        layers=layers,
        source_geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features}
        ),
    )


@pytest.mark.parametrize("explicit", [False, True])
def test_default_editor_preserves_external_project_and_property_aliases(explicit):
    project = source_project()
    before = project.model_dump_json()
    features = project.source_geometry.feature_collection["features"]
    properties = [feature["properties"] for feature in features]
    geometry = [feature["geometry"] for feature in features]
    opened = editor_source.open_source_editor(
        project, **({"consume_owned": False} if explicit else {})
    )
    assert project.model_dump_json() == before
    actual = opened.geometry.feature_collection["features"]
    assert actual is not features
    for original, result, props, shape in zip(features, actual, properties, geometry):
        assert result is not original and result["properties"] is not props
        assert result["geometry"] is shape
    actual[0]["properties"]["kind"] = "building"
    assert project.model_dump_json() == before


def test_owned_editor_preserves_exact_output_and_reuses_metadata_dictionaries():
    project = source_project()
    expected = editor_source.open_source_editor(project).model_dump_json()
    features = project.source_geometry.feature_collection["features"]
    identities = [(id(f), id(f["properties"]), id(f["geometry"])) for f in features]
    coordinates = deepcopy([f["geometry"] for f in features])
    opened = editor_source.open_source_editor(project, consume_owned=True)
    assert opened.model_dump_json() == expected
    assert opened.geometry.feature_collection["features"] is features
    assert [
        (id(f), id(f["properties"]), id(f["geometry"])) for f in features
    ] == identities
    assert [f["geometry"] for f in features] == coordinates
    assert [f["properties"]["kind"] for f in features] == ["road", "utility", "ignore"]
    assert "utility_context" not in features[2]["properties"]
    assert opened.source_geometry is None and opened.map_ready


def test_owned_editor_refuses_noneditable_source_before_mutation():
    project = source_project()
    project.import_status = ImportStatus(editability=ImportEditability.READ_ONLY)
    before = project.model_dump_json()
    with pytest.raises(ValueError):
        editor_source.open_source_editor(project, consume_owned=True)
    assert project.model_dump_json() == before


@pytest.mark.parametrize("failure", [ValueError, OperationCancelled])
def test_owned_editor_failure_does_not_publish_partial_import(
    tmp_path, monkeypatch, failure
):
    probe = near_closed_building_proposal_probe()
    probe["capture_mode"] = "live_document"
    content = json.dumps(probe).encode()
    source_path = tmp_path / "capture.json"
    source_path.write_bytes(content)
    runtime = create_runtime(tmp_path / "isolated.sqlite3")
    try:
        project = runtime.application.create_project("Неполная операция")
        before = project.model_dump_json()
        original = editor_source.apply_source_roles

        def interrupt_roles(current, features):
            original(current, features[:1])
            raise failure("Подготовка прервана")

        monkeypatch.setattr(editor_source, "apply_source_roles", interrupt_roles)
        with pytest.raises(failure):
            runtime.application._imports.import_autocad_live_file(
                project.id,
                "capture.json",
                source_path,
                sha256(content).hexdigest(),
                autocad_version="2027",
                target="macos-arm64",
            )
        assert runtime.project_repository.get(project.id).model_dump_json() == before
        assert runtime.project_repository.get_source(project.id) is None
        assert source_path.read_bytes() == content
    finally:
        runtime.close()
