from pathlib import Path

import ezdxf
import pytest

from app.dxf_import.adapters import EzdxfReader


@pytest.mark.parametrize("version,encoding", [("R2000", "cp1251"), ("R2018", "utf8")])
def test_prepared_file_stream_matches_upload_geometry_and_layer_names(
    tmp_path, version, encoding
):
    document = ezdxf.new(version)
    document.units = 6
    document.encoding = encoding
    document.layers.new("Границы$0$СУЩ_СЕТИ_Газопровод")
    document.modelspace().add_lwpolyline(
        [(0, 0, 0.5), (10, 0, 0), (10, 20, 0)],
        format="xyb",
        close=True,
        dxfattribs={"layer": "Границы$0$СУЩ_СЕТИ_Газопровод"},
    )
    block = document.blocks.new("Вставка")
    block.add_circle((0, 0), 2)
    document.modelspace().add_blockref("Вставка", (50, 60), dxfattribs={"rotation": 45})
    path = tmp_path / "source.dxf"
    document.saveas(path)
    content = path.read_bytes()
    reader = EzdxfReader()
    assert (
        reader.read_prepared_file(path).model_dump()
        == reader.read(path.name, content).model_dump()
    )
    assert path.read_bytes() == content


def test_fixture_file_stream_matches_existing_upload():
    path = Path(__file__).parents[3] / "fixtures/site.dxf"
    reader = EzdxfReader()
    assert (
        reader.read_prepared_file(path).model_dump()
        == reader.read(path.name, path.read_bytes()).model_dump()
    )
