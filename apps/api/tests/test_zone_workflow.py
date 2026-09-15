"""The existing compiler can bind zone actions without accepting model geometry."""
import json

import pytest
from pydantic import ValidationError
from shapely.geometry import box, mapping

from app.agent_runtime.zone_workflow import (
    ZoneGeometryReference, ZoneIntent, bind_zone_intent, prepare_zone_change,
    verify_zone_preview, zone_geometry_references,
)
from app.planting_zone_changes import ZoneChangePreview, _preview_digest, get_zone_change_service
from app.projects.concurrency import ProjectVersionConflict
from test_planting_zone_changes import approval, seeded


def project_with_contour():
    app, project = seeded()
    project.geometry.feature_collection["features"].append({
        "type": "Feature", "id": "source-area", "properties": {"kind": "allowed", "label": "Точный контур"},
        "geometry": mapping(box(100, 100, 130, 130)),
    })
    app.repository.save(project)
    project = app.get(project.id)
    reference = next(item["reference"] for item in zone_geometry_references(project) if item["reference"]["feature_id"] == "source-area")
    return app, project, ZoneGeometryReference(**reference)


@pytest.mark.parametrize("text,operation,label,zone", [
    ("Переименуй участок east в «Сад».", "update", "Сад", "east"),
    ("Удали участок east.", "delete", None, "east"),
    ("Участок East удали.", "delete", None, "east"),
])
def test_source_bound_rename_and_delete_prepare_exact_full_population(text, operation, label, zone):
    app, project = seeded()
    bound = bind_zone_intent(text, project, ZoneIntent(operation=operation, label=label))
    assert bound.draft.zone_id == zone
    preview = prepare_zone_change(app, project.id, bound)
    verify_zone_preview(bound, preview)
    assert app.get(project.id).state_version == project.state_version
    result = get_zone_change_service(app).commit(project.id, approval(preview))
    assert result.operation == operation and result.target_zone_id == zone
    assert app.get(project.id).plan.objects == project.plan.objects


@pytest.mark.parametrize("text,label", [
    ("Создай участок из контура source-area.", None),
    ("Создай участок «Сад» из контура source-area.", "Сад"),
    ("Создай участок по контуру «Точный контур».", None),
])
def test_create_uses_the_exact_authoritative_feature_geometry(text, label):
    app, project, reference = project_with_contour()
    bound = bind_zone_intent(text, project, ZoneIntent(operation="create", label=label, geometry_reference=reference))
    feature = next(item for item in project.geometry.feature_collection["features"] if item.get("id") == reference.feature_id)
    assert bound.draft.geometry == json.loads(json.dumps(feature["geometry"]))
    preview = prepare_zone_change(app, project.id, bound)
    verify_zone_preview(bound, preview)
    assert preview.after_zones[-1].geometry == bound.draft.geometry
    result = get_zone_change_service(app).commit(project.id, approval(preview))
    assert result.after_zones[-1].geometry == bound.draft.geometry


def test_update_geometry_also_requires_a_source_grounded_reference():
    app, project, reference = project_with_contour()
    bound = bind_zone_intent("Измени контур участка east по контуру source-area.", project,
                             ZoneIntent(operation="update", target_zone_id="east", geometry_reference=reference))
    preview = prepare_zone_change(app, project.id, bound)
    verify_zone_preview(bound, preview)
    assert preview.after_zones[0] == preview.before_zones[0]
    assert preview.after_zones[1].id == "east" and preview.after_zones[1].geometry == bound.draft.geometry


@pytest.mark.parametrize("text,intent", [
    ("Удали участок east.", ZoneIntent(operation="update", target_zone_id="east", label="Сад")),
    ("Удали участок east.", ZoneIntent(operation="delete", target_zone_id="west")),
    ("Переименуй участок east в «Сад».", ZoneIntent(operation="update", target_zone_id="east", label="Лес")),
    ("Удали участок east и участок west.", ZoneIntent(operation="delete", target_zone_id="east")),
    ("Не удаляй участок east.", ZoneIntent(operation="delete", target_zone_id="east")),
    ("Удали дерево на участке east.", ZoneIntent(operation="delete", target_zone_id="east")),
    ("Удали часть участка east.", ZoneIntent(operation="delete", target_zone_id="east")),
    ("Проверь участок east.", ZoneIntent(operation="delete", target_zone_id="east")),
    ("Удали участок east и создай новый участок.", ZoneIntent(operation="delete", target_zone_id="east")),
])
def test_model_operation_target_name_and_partial_scope_substitution_are_rejected(text, intent):
    app, project = seeded()
    with pytest.raises(ValueError):
        bind_zone_intent(text, project, intent)
    assert app.get(project.id).state_version == project.state_version


def test_model_cannot_invent_a_polygon_or_claim_user_geometry_transport():
    for extra in [{"geometry": mapping(box(100, 100, 130, 130))}, {"user_geometry": mapping(box(100, 100, 130, 130))}]:
        with pytest.raises(ValidationError):
            ZoneIntent(operation="create", **extra)


@pytest.mark.parametrize("change", [
    {"project_id": "another-project"}, {"state_version": 999}, {"geometry_version": 999},
    {"feature_id": "missing-feature"}, {"geometry_digest": "a" * 64},
])
def test_feature_identity_versions_and_full_geometry_digest_are_mandatory(change):
    _, project, reference = project_with_contour()
    with pytest.raises(ValueError):
        bind_zone_intent("Создай участок из контура source-area.", project,
            ZoneIntent(operation="create", geometry_reference=reference.model_copy(update=change)))


@pytest.mark.parametrize("text", ["Создай участок.", "Создай два участка из контура source-area.",
                                  "Создай 2 участка из контура source-area.",
                                  "Создай участок из контура source-area и создай участок из контура source-area.",
                                  "Создай северную часть участка из контура source-area."])
def test_missing_reference_anchor_or_unimplemented_geometry_qualifier_requires_clarification(text):
    _, project, reference = project_with_contour()
    with pytest.raises(ValueError):
        bind_zone_intent(text, project, ZoneIntent(operation="create", geometry_reference=reference))


def test_duplicate_feature_identity_cannot_resolve_to_an_arbitrary_contour():
    _, project, reference = project_with_contour()
    project.geometry.feature_collection["features"].append({
        "type": "Feature", "id": "source-area", "properties": {"kind": "allowed"},
        "geometry": mapping(box(140, 140, 160, 160)),
    })
    with pytest.raises(ValueError):
        bind_zone_intent("Создай участок из контура source-area.", project,
                         ZoneIntent(operation="create", geometry_reference=reference))


def test_duplicate_feature_label_requires_an_explicit_feature_id():
    _, project, reference = project_with_contour()
    project.geometry.feature_collection["features"].append({
        "type": "Feature", "id": "another-area", "properties": {"kind": "allowed", "label": "Точный контур"},
        "geometry": mapping(box(140, 140, 160, 160)),
    })
    with pytest.raises(ValueError, match="неоднозначно"):
        bind_zone_intent("Создай участок по контуру «Точный контур».", project,
                         ZoneIntent(operation="create", geometry_reference=reference))
    bound = bind_zone_intent("Создай участок из контура source-area.", project,
                             ZoneIntent(operation="create", geometry_reference=reference))
    assert bound.intent.geometry_reference.feature_id == "source-area"


def test_explicit_contour_id_does_not_erase_another_contour_named_by_label():
    _, project, reference = project_with_contour()
    project.geometry.feature_collection["features"].append({
        "type": "Feature", "id": "another-area", "properties": {"kind": "allowed", "label": "Новый контур"},
        "geometry": mapping(box(140, 140, 160, 160)),
    })
    with pytest.raises(ValueError, match="неоднозначно"):
        bind_zone_intent('Создай участок по контуру source-area и «Новый контур».', project,
                         ZoneIntent(operation="create", geometry_reference=reference))


def test_a_feature_sharing_the_rename_target_label_does_not_authorize_a_new_polygon():
    _, project, reference = project_with_contour()
    feature = next(item for item in project.geometry.feature_collection["features"] if item.get("id") == "source-area")
    feature["properties"]["label"] = "East"
    with pytest.raises(ValueError, match="Контур не указан"):
        bind_zone_intent("Переименуй участок east в «Сад».", project,
                         ZoneIntent(operation="update", label="Сад", geometry_reference=reference))


def test_saved_binding_is_rechecked_against_source_and_current_project():
    app, project, reference = project_with_contour()
    bound = bind_zone_intent("Создай участок из контура source-area.", project,
                             ZoneIntent(operation="create", geometry_reference=reference))
    substituted = bound.model_copy(update={"draft": bound.draft.model_copy(update={"geometry": mapping(box(140, 140, 160, 160))})})
    with pytest.raises(ValueError, match="основания"):
        prepare_zone_change(app, project.id, substituted)
    project.name = "Changed"
    app.repository.save(project)
    with pytest.raises(ProjectVersionConflict):
        prepare_zone_change(app, project.id, bound)


@pytest.mark.parametrize("tampering", ["untouched_zone", "target_geometry", "target_id", "operation", "before", "plants"])
def test_full_effect_verification_rejects_tampering_even_with_a_recomputed_digest(tampering):
    app, project = seeded()
    bound = bind_zone_intent("Переименуй участок east в «Сад».", project,
                             ZoneIntent(operation="update", label="Сад", target_zone_id="east"))
    preview = prepare_zone_change(app, project.id, bound)
    data = preview.model_dump(mode="json")
    if tampering == "untouched_zone":
        data["after_zones"][0]["label"] = "Also rename West"
    elif tampering == "target_geometry":
        data["after_zones"][1]["geometry"] = mapping(box(410, 10, 490, 90))
    elif tampering == "target_id":
        data["target_zone_id"] = "west"
    elif tampering == "operation":
        data["operation"] = "delete"
    elif tampering == "before":
        data["before_zones"][0]["label"] = "Alter original"
    else:
        data["planting_digest"] = "0" * 64
    changed = ZoneChangePreview.model_validate(data)
    changed = changed.model_copy(update={"digest": _preview_digest(changed)})
    with pytest.raises(ValueError):
        verify_zone_preview(bound, changed)


def test_compact_metadata_is_not_full_proposal_evidence():
    app, project = seeded()
    bound = bind_zone_intent("Удали участок east.", project, ZoneIntent(operation="delete"))
    preview = prepare_zone_change(app, project.id, bound)
    with pytest.raises(ValidationError):
        verify_zone_preview(bound, {"id": preview.id, "digest": preview.digest, "can_apply": True})


def test_blocked_occupied_deletion_is_reportable_but_not_approval_evidence():
    app, project = seeded()
    bound = bind_zone_intent("Удали участок west.", project, ZoneIntent(operation="delete"))
    preview = prepare_zone_change(app, project.id, bound)
    assert not preview.can_apply and preview.affected_planting_ids == ("tree-west",)
    verify_zone_preview(bound, preview, require_applicable=False)
    with pytest.raises(ValueError):
        verify_zone_preview(bound, preview)


@pytest.mark.parametrize("target", ["Допустимая область 2", "участок 2", "зону №2"])
def test_duplicate_raw_labels_use_the_same_numbered_names_as_project_context(target):
    app, project = seeded()
    for zone in project.planting_zones:
        zone.label = "Допустимая область"
    app.repository.save(project)
    project = app.get(project.id)
    bound = bind_zone_intent(f"Удали {target}.", project, ZoneIntent(operation="delete", target_zone_id="east"))
    assert bound.draft.zone_id == "east"
    preview = prepare_zone_change(app, project.id, bound)
    assert preview.target_zone_id == "east" and preview.can_apply


@pytest.mark.parametrize("source", ["Удали Допустимая область.", "Удали участок 99 east.", "Удали участок 1 east.", "Удали участок east-новый."])
def test_ambiguous_names_unknown_numbers_and_partial_ids_do_not_authorize_a_target(source):
    _, project = seeded()
    for zone in project.planting_zones:
        zone.label = "Допустимая область"
    with pytest.raises(ValueError):
        bind_zone_intent(source, project, ZoneIntent(operation="delete", target_zone_id="east"))


def test_numbered_display_label_cannot_hide_model_target_substitution():
    _, project = seeded()
    for zone in project.planting_zones:
        zone.label = "Допустимая область"
    with pytest.raises(ValueError):
        bind_zone_intent("Удали Допустимая область 2.", project, ZoneIntent(operation="delete", target_zone_id="west"))


def test_numeric_prefix_in_an_exact_uuid_is_not_a_visible_zone_number():
    _, project = seeded()
    project.planting_zones[1].id = "1234-abcd-6789"
    bound = bind_zone_intent("Удали участок 1234-abcd-6789.", project, ZoneIntent(operation="delete"))
    assert bound.draft.zone_id == "1234-abcd-6789"


def test_geometry_source_zone_is_not_mistaken_for_the_edit_target():
    _, project, _ = project_with_contour()
    reference = ZoneGeometryReference(**next(item["reference"] for item in zone_geometry_references(project)
        if item.get("label") == "West"))
    bound = bind_zone_intent("Измени контур участка east по контуру West.", project,
        ZoneIntent(operation="update", target_zone_id="east", geometry_reference=reference))
    assert bound.draft.zone_id == "east"


@pytest.mark.parametrize("label", ["Сад по берегу", "Лес из берёз"])
def test_relations_inside_quoted_target_name_do_not_request_geometry_changes(label):
    _, project = seeded()
    project.planting_zones[1].label = label
    bound = bind_zone_intent(f"Переименуй участок «{label}» в «Новый сад».", project,
                            ZoneIntent(operation="update", label="Новый сад"))
    assert bound.draft.zone_id == "east" and bound.draft.geometry is None
    assert bound.draft.label == "Новый сад"


def test_area_metadata_is_verified_against_full_geometry_even_if_digest_is_recomputed():
    app, project = seeded()
    bound = bind_zone_intent("Удали участок east.", project, ZoneIntent(operation="delete"))
    preview = prepare_zone_change(app, project.id, bound)
    preview = preview.model_copy(update={"before_area_m2": preview.before_area_m2 + 1})
    preview = preview.model_copy(update={"digest": _preview_digest(preview)})
    with pytest.raises(ValueError, match="Площадь"):
        verify_zone_preview(bound, preview)
