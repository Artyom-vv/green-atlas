from io import BytesIO, StringIO
from typing import Any

import ezdxf
from ezdxf.document import Drawing
from ezdxf.lldxf.tagger import binary_tags_loader

from app.contracts import ExportArtifact, Project
from app.dxf_import.encoding import decode_text_dxf
from app.dxf_import.units import meters_per_dxf_unit


GREEN_ATLAS_APP_ID = "GREEN_ATLAS"
GREEN_ATLAS_SCHEMA = "green-atlas:1"
PLANTING_LAYER_BASES = {
    "tree": ("GREEN_ATLAS_TREES", 94),
    "shrub": ("GREEN_ATLAS_SHRUBS", 83),
}


def _read_document(content: bytes) -> Any:
    if content.startswith(b"AutoCAD Binary DXF"):
        return Drawing.load(binary_tags_loader(content))
    return ezdxf.read(StringIO(decode_text_dxf(content)))


def _new_layer_name(document: Any, base_name: str) -> str:
    """Create a result layer without merging planting into source context."""

    candidate = base_name
    index = 2
    while candidate in document.layers:
        candidate = f"{base_name}_{index}"
        index += 1
    return candidate


class DxfRoundTripWriter:
    """Round-trip writer: source entities stay intact, planting is added to new layers."""

    def create(self, project: Project, source_content: bytes) -> tuple[ExportArtifact, bytes]:
        if project.plan is None:
            raise ValueError("Нельзя экспортировать проект без плана")
        try:
            document = _read_document(source_content)
        except Exception as error:
            raise ValueError("Не удалось повторно открыть исходный DXF") from error
        planting_layers = {
            kind: _new_layer_name(document, base_name)
            for kind, (base_name, _color) in PLANTING_LAYER_BASES.items()
        }
        for kind, layer_name in planting_layers.items():
            document.layers.add(layer_name, color=PLANTING_LAYER_BASES[kind][1])
        if GREEN_ATLAS_APP_ID not in document.appids:
            document.appids.add(GREEN_ATLAS_APP_ID)

        unit_code = int(document.header.get("$INSUNITS", 0) or 0)
        meters_per_unit = meters_per_dxf_unit(unit_code) or 1.0
        modelspace = document.modelspace()
        for object_ in project.plan.objects:
            layer = planting_layers[object_.kind]
            entity = modelspace.add_circle(
                (object_.x / meters_per_unit, object_.y / meters_per_unit),
                radius=object_.radius / meters_per_unit,
                dxfattribs={"layer": layer},
            )
            xdata: list[tuple[int, str | float]] = [
                (1000, f"schema={GREEN_ATLAS_SCHEMA}"),
                (1000, f"object_id={object_.id}"),
                (1000, f"kind={object_.kind}"),
                (1000, f"size_class={object_.size_class}"),
                (1000, f"layout_radius_m={object_.layout_radius_m or object_.radius}"),
                (1000, f"locked={'true' if object_.locked else 'false'}"),
            ]
            if object_.species_revision_id:
                xdata.append((1000, f"species_revision_id={object_.species_revision_id}"))
            if object_.pattern_id:
                xdata.append((1000, f"pattern_id={object_.pattern_id}"))
            for group_id in sorted(object_.group_ids):
                xdata.append((1000, f"group_id={group_id}"))
            if object_.planting_zone_id:
                xdata.append((1000, f"planting_zone_id={object_.planting_zone_id}"))
            entity.set_xdata(GREEN_ATLAS_APP_ID, xdata)
        # A binary DXF is a valid source format, not an import-only special
        # case. Preserve that representation on export as well: callers that
        # use binary DXF because of size or an established CAD exchange must
        # not silently receive a text file after adding the planting layers.
        if source_content.startswith(b"AutoCAD Binary DXF"):
            stream = BytesIO()
            document.write(stream, fmt="bin")
            content = stream.getvalue()
        else:
            stream = StringIO()
            document.write(stream)
            # R2000/R2004 DXF can legitimately use a legacy code page (for
            # example CP1251 for Russian layer names and labels). ``write``
            # gives us Unicode text; encoding it unconditionally as UTF-8
            # leaves the original $DWGCODEPAGE lying to CAD consumers. Let
            # ezdxf select the format-required output encoding instead.
            content = stream.getvalue().encode(document.output_encoding, errors="dxfreplace")
        artifact = ExportArtifact(filename=f"{project.name.lower().replace(' ', '_')}_plan.dxf", status="ready", size=len(content), download_url="")
        return artifact, content
