"""Shared target references preserve the exact command and ambiguous source."""
import pytest

from app.agent_runtime.target_sources import resolve_zone_reference, resolve_zone_target, remove_mentions
from app.agent_runtime.zone_sources import _target, _mention, _remove_mentions
from test_planting_zone_changes import seeded


@pytest.fixture
def project():
    return seeded()[1]


@pytest.mark.parametrize("source", ["Покажи участок east на карте.", "Покажи участок «East» на карте.",
    "Покажи East на карте.", "east", "«East»", "Участок east удали."])
def test_one_exact_reference_keeps_its_source_spans(project, source):
    reference = resolve_zone_reference(source, project)
    assert reference.target_id == "east"
    assert len(reference.remaining_text) == len(source)
    for start, end in reference.spans:
        assert source[start:end] in reference.anchors
        assert reference.remaining_text[start:end].isspace()
    assert _target(source, project) == resolve_zone_target(source, project)


@pytest.mark.parametrize("label", ["Сад участок 7 у реки", "Удали участок 7", "Выбранный сад", "Сад по берегу"])
def test_quoted_name_is_atomic_including_numbers_and_verbs(project, label):
    project.planting_zones[0].label = "Другая зона 7"
    project.planting_zones[1].label = label
    source = f"Покажи участок «{label}» на карте."
    reference = resolve_zone_reference(source, project)
    assert reference.target_id == "east"
    assert reference.anchors == (f"«{label}»",)
    assert reference.remaining_text.strip() == "Покажи участок " + " " * (len(label) + 2) + " на карте."


@pytest.mark.parametrize("verb", ["Покажи", "Открой"])
@pytest.mark.parametrize("quoted", [True, False])
def test_command_word_name_cannot_remove_the_command_occurrence(project, verb, quoted):
    project.planting_zones[1].label = verb
    target = f"«{verb}»" if quoted else verb
    source = f"{verb} участок {target} на карте."
    reference = resolve_zone_reference(source, project)
    assert reference.target_id == "east"
    assert reference.remaining_text.startswith(verb + " ")
    assert reference.remaining_text.count(verb) == 1
    assert remove_mentions(source, reference.anchors).startswith(verb + " ")


def test_command_matching_project_name_is_not_itself_a_target(project):
    project.planting_zones[1].label = "Покажи"
    source = "Покажи участок на карте."
    reference = resolve_zone_reference(source, project)
    assert reference.target_id is None and reference.remaining_text == source


@pytest.mark.parametrize("target", ["Допустимая область 2", "участок 2", "зону №2"])
def test_canonical_display_labels_and_numbers_share_the_directory(project, target):
    for zone in project.planting_zones:
        zone.label = "Допустимая область"
    assert resolve_zone_reference(f"Покажи {target} на карте.", project).target_id == "east"


def test_exact_id_disambiguates_its_shared_raw_name(project):
    for zone in project.planting_zones:
        zone.label = "Сад"
    with pytest.raises(ValueError):
        resolve_zone_reference("Покажи участок «Сад» на карте.", project)
    reference = resolve_zone_reference("Покажи участок ID east «Сад» на карте.", project)
    assert reference.target_id == "east" and "Сад" not in reference.remaining_text


@pytest.mark.parametrize("source", ["Покажи участок east и «Западный сад» на карте.",
    "Покажи участок east и west на карте.", "Покажи участок east и участок 7 на карте."])
def test_explicit_id_cannot_erase_another_named_target(project, source):
    project.planting_zones[0].label = "Западный сад" if "7" not in source else "Западный сад 7"
    with pytest.raises(ValueError):
        resolve_zone_reference(source, project)


@pytest.mark.parametrize("source", ["Покажи участок «Неизвестный участок 7» на карте.",
    "Покажи участок east-новый на карте."])
def test_unknown_quoted_reference_and_partial_id_are_retained(project, source):
    project.planting_zones[0].label = "Западный сад 7"
    reference = resolve_zone_reference(source, project)
    assert reference.target_id is None and reference.remaining_text == source and not reference.spans


def test_unknown_number_never_falls_back_to_existing_id(project):
    with pytest.raises(ValueError):
        resolve_zone_reference("Покажи участок 99 east на карте.", project)


def test_unknown_quoted_label_survives_next_to_a_known_id(project):
    source = "Покажи участок east и «Участок 99» на карте."
    reference = resolve_zone_reference(source, project)
    assert reference.target_id == "east"
    assert "«Участок 99»" in reference.remaining_text


def test_numeric_id_requires_id_marker_and_uuid_prefix_is_not_a_number(project):
    project.planting_zones[1].id = "123"
    assert resolve_zone_reference("Покажи участок ID 123 на карте.", project).target_id == "123"
    with pytest.raises(ValueError):
        resolve_zone_reference("Покажи участок 123 на карте.", project)
    project.planting_zones[1].id = "1234-abcd-6789"
    assert resolve_zone_reference("Покажи участок 1234-abcd-6789 на карте.", project).target_id == "1234-abcd-6789"


def test_compatibility_aliases_keep_literal_boundary_behavior():
    assert _mention("east", "east") and not _mention("east-new", "east")
    assert _remove_mentions("east west", ["east"]) == "  west"
