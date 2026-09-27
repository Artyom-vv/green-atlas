"""Real DXF, isolated in-memory proposal. Never applies or saves to live server."""
import json
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent_memory import TaskPatch, TaskState
from app.agent_interpreter import project_context
from app.agent_planning import prepare_placement
from app.application import ProjectApplication
from app.contracts import Project
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.query_adapters import IndexedGeometryQuery
from app.planning.patterns import ShapelyCandidateGenerator
from app.projects.adapters import InMemoryProjectRepository
from app.validation.adapters import RuleBasedPlanValidator


def isolated_application(project):
    repository = InMemoryProjectRepository()
    repository.create(project)
    return ProjectApplication(repository=repository, operation_repository=None, history=None, dxf_reader=None,
        geometry=ShapelyGeometryEngine(), geometry_query=IndexedGeometryQuery(), validator=RuleBasedPlanValidator(),
        writer=None, candidate_generator=ShapelyCandidateGenerator())


def main():
    with urlopen("http://127.0.0.1:8001/api/projects/0216cb9e-23ac-429e-bd1a-08815164ef0f?include_geometry=true", timeout=30) as response:
        project = Project.model_validate_json(response.read())
    zones = [zone["id"] for zone in project_context(project)["zones"] if zone["label"] == "Допустимая область 6"]
    assert zones and project.geometry
    application = isolated_application(project)
    before = application.get(project.id).model_dump_json()
    task = TaskState().amended(TaskPatch(operation="place", plant_kind="tree", scope="zones", zone_ids=zones,
        quantity=100, quantity_mode="target", arrangement="building_contour", species_mode="specified",
        species_revision_ids=["quercus-robur@2026-08-28.1"]), "evaluation")
    result = prepare_placement(application, project.id, task)
    assert before == application.get(project.id).model_dump_json()
    assert result["found"] > 0, "No real candidate generated"
    assert result["change_set"]["can_apply"]
    print(json.dumps({key: result[key] for key in ("requested", "found", "shortfall", "species_revision_id", "requires_confirmation")}, ensure_ascii=False))
    print("PASS: real DXF preview only. Live project not modified.")


if __name__ == "__main__":
    main()
