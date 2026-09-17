from contextlib import nullcontext
from io import BytesIO, StringIO

import ezdxf
import pytest
from ezdxf.lldxf.tagwriter import TagCollector
from ezdxf.sections.acdsdata import AcDsDataSection, new_acis_record

from app.dxf_import import acis_lookup, adapters
from app.dxf_import.acis_lookup import indexed_acis_lookup


def record_tags(section):
    collector = TagCollector()
    for record in section.entities:
        record.export_dxf(collector)
    return collector.tags


def acis_block_source(*, binary=False):
    document = ezdxf.new("R2018")
    document.units = ezdxf.units.M
    block = document.blocks.new("Survey")
    region = block.add_region(dxfattribs={"layer": "ACIS_CONTEXT"})
    # Opaque, multi-chunk payload: this optimization must not parse ACIS.
    region.sab = bytes(range(256)) * 3
    block.add_line((0, 0), (1, 1))
    document.modelspace().add_blockref(block.name, (10, 20))
    document.modelspace().add_blockref(block.name, (20, 40))
    stream = BytesIO() if binary else StringIO()
    document.write(stream, fmt="bin" if binary else "asc")
    payload = stream.getvalue()
    return payload if binary else payload.encode("utf8")


def test_lookup_preserves_first_match_missing_empty_and_multichunk(monkeypatch):
    document = ezdxf.new("R2018")
    section = document.acdsdata
    first = new_acis_record("ABC", bytes(range(256)) * 3)
    duplicate = new_acis_record("ABC", b"not the first record")
    empty = new_acis_record("DEF", b"")
    # An unrelated ACDSRECORD with the same handle is not ACIS data.
    unrelated = new_acis_record("ABC", b"not ACIS")
    unrelated.sections = [s for s in unrelated.sections if s.name != "ASM_Data"]
    section.entities.extend([unrelated, first, duplicate, empty])
    handles = ("ABC", "DEF", "abc", "MISSING")
    expected = {handle: section.get_acis_data(handle) for handle in handles}
    before = record_tags(section)
    sdk_lookup = AcDsDataSection.find_acis_record
    requests = []
    original_handle = acis_lookup.acis_entity_handle

    def count_handle(record):
        requests.append(record)
        return original_handle(record)

    monkeypatch.setattr(acis_lookup, "acis_entity_handle", count_handle)
    with indexed_acis_lookup(document):
        assert section.find_acis_record("ABC") is first
        assert section.find_acis_record("DEF") is empty
        assert section.find_acis_record("abc") is None
        assert section.find_acis_record("MISSING") is None
        for _ in range(20):
            assert {h: section.get_acis_data(h) for h in handles} == expected
        # Each ACIS record is inspected once, independent of lookup count.
        assert requests == [first, duplicate, empty]
        assert AcDsDataSection.find_acis_record is sdk_lookup
        assert record_tags(section) == before

    assert "find_acis_record" not in vars(section)
    assert section.find_acis_record.__func__ is sdk_lookup
    assert record_tags(section) == before


@pytest.mark.parametrize("binary", [False, True])
def test_insert_copy_loads_sab_lazily_without_changing_original_records(binary):
    source = acis_block_source(binary=binary)
    document = adapters._read_document(source)
    section = document.acdsdata
    region = document.blocks["Survey"].query("REGION")[0]
    before_tags = record_tags(section)
    before_attributes = region.dxf.all_existing_dxf_attribs().copy()
    original_entities = tuple(document.entitydb)
    assert region._sab == b""  # SDK's lazy cache is initially empty.
    assert not region._update

    with indexed_acis_lookup(document):
        assert region._sab == b""  # Building the index does not read payloads.
        for insert in document.modelspace().query("INSERT"):
            copies = list(insert.virtual_entities())
            assert [entity.dxftype() for entity in copies] == ["REGION", "LINE"]
            assert copies[0].sab == bytes(range(256)) * 3
            assert copies[1].dxf.start.isclose(insert.dxf.insert)
        assert not region._update  # Getter caching does not mark source modified.

    assert region.dxf.all_existing_dxf_attribs() == before_attributes
    assert record_tags(section) == before_tags
    assert tuple(document.entitydb) == original_entities
    assert "find_acis_record" not in vars(section)


def test_lookup_is_restored_on_failure_and_later_mutations_use_sdk():
    document = ezdxf.new("R2018")
    section = document.acdsdata
    section.new_acis_data("ABC", b"original")
    with pytest.raises(RuntimeError, match="normalization failed"):
        with indexed_acis_lookup(document):
            assert section.get_acis_data("ABC") == b"original"
            raise RuntimeError("normalization failed")
    assert "find_acis_record" not in vars(section)
    section.del_acis_data("ABC")
    section.new_acis_data("NEW", b"new")
    section.set_acis_data("NEW", b"updated")
    assert section.get_acis_data("ABC") == b""
    assert section.get_acis_data("NEW") == b"updated"


def test_another_document_and_existing_instance_override_are_untouched():
    document = ezdxf.new("R2018")
    other = ezdxf.new("R2018")
    document.acdsdata.new_acis_data("ABC", b"indexed")
    other.acdsdata.new_acis_data("ABC", b"other")
    original_lookup = other.acdsdata.find_acis_record
    with indexed_acis_lookup(document):
        assert "find_acis_record" not in vars(other.acdsdata)
        assert other.acdsdata.find_acis_record == original_lookup
        assert other.acdsdata.get_acis_data("ABC") == b"other"
        outer_lookup = document.acdsdata.find_acis_record
        with indexed_acis_lookup(document):
            assert document.acdsdata.find_acis_record is outer_lookup
        assert document.acdsdata.find_acis_record is outer_lookup
    assert "find_acis_record" not in vars(document.acdsdata)


def test_other_sdk_versions_keep_their_lookup(monkeypatch):
    document = ezdxf.new("R2018")
    document.acdsdata.new_acis_data("ABC", b"original")
    monkeypatch.setattr(ezdxf, "__version__", "2.0.0")
    with indexed_acis_lookup(document):
        assert "find_acis_record" not in vars(document.acdsdata)
        assert document.acdsdata.get_acis_data("ABC") == b"original"


@pytest.mark.parametrize("binary", [False, True])
def test_reader_result_and_upload_bytes_match_unindexed_sdk(monkeypatch, binary):
    content = bytearray(acis_block_source(binary=binary))
    original = bytes(content)
    actual = adapters.EzdxfReader().read("survey.dxf", content)
    monkeypatch.setattr(adapters, "indexed_acis_lookup", lambda document: nullcontext())
    expected = adapters.EzdxfReader().read("survey.dxf", content)
    assert actual.model_dump() == expected.model_dump()
    assert bytes(content) == original
    # Faster access must not falsely claim ACIS geometry has been normalized.
    acis_layer = next(
        layer for layer in actual.layers if layer.source_name == "ACIS_CONTEXT"
    )
    assert not acis_layer.geometry_complete


def test_reader_restores_lookup_when_normalization_raises(monkeypatch):
    document = adapters._read_document(acis_block_source())
    monkeypatch.setattr(adapters, "_read_document", lambda content: document)
    reader = adapters.EzdxfReader()

    def fail_normalization(document):
        assert "find_acis_record" in vars(document.acdsdata)
        raise RuntimeError("normalization failed")

    monkeypatch.setattr(reader, "_normalize_document", fail_normalization)
    with pytest.raises(RuntimeError, match="normalization failed"):
        reader.read("source.dxf", b"isolated test document")
    assert "find_acis_record" not in vars(document.acdsdata)
