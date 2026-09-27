"""Local model + actual project references. Reads project, never writes it."""
import json
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent_interpreter import interpret_task, project_context
from app.agent_memory import TaskState
from app.agent_perception import MapContext, bind_selection, perceive
from app.contracts import Project
from app.agent_planning import prepare_task
from evaluate_agent_placement import isolated_application


def main():
    with urlopen("http://127.0.0.1:8001/api/projects/0216cb9e-23ac-429e-bd1a-08815164ef0f?include_geometry=true", timeout=30) as response:
        project = Project.model_validate_json(response.read())
    selected = [obj.id for obj in project.plan.objects if obj.kind == "tree"][:2]
    assert len(selected) == 2, "Need two actual trees for the selection test"
    perception = perceive(project, MapContext(state_version=project.state_version, object_ids=selected))
    context = {**project_context(project), "map_context": perception}
    text = "У этих выбранных деревьев поменяйте породу на дуб. Остальные посадки не меняйте."
    patch, interpretation = interpret_task(text, TaskState(), [], context, "qwen3.5:4b")
    patch = bind_selection(patch, perception)
    state = TaskState().amended(patch, "selection")
    print(json.dumps({"interpretation": interpretation.model_dump(), "task": state.model_dump()}, ensure_ascii=False), flush=True)
    assert state.values.operation == "edit", "Edit not recognized"
    assert state.values.scope == "objects" and set(state.values.object_ids or []) == set(selected), "Selection not bound exactly"
    assert state.values.species_revision_ids == ["quercus-robur@2026-08-28.1"], "Oak not resolved"
    application = isolated_application(project)
    before = application.get(project.id).model_dump_json()
    prepared = prepare_task(application, project.id, state)
    preview = prepared["change_set"]
    assert not preview["additions"] and not preview["deletion_ids"]
    assert {obj["id"] for obj in preview["updates"]}.issubset(set(selected))
    original = {obj.id: obj for obj in project.plan.objects}
    for obj in preview["updates"]:
        previous = original[obj["id"]]
        assert (obj["x"], obj["y"], obj["group_ids"]) == (previous.x, previous.y, previous.group_ids)
        assert obj["species_revision_id"] == "quercus-robur@2026-08-28.1"
    assert before == application.get(project.id).model_dump_json()
    print(json.dumps({"can_apply": preview["can_apply"], "updates": len(preview["updates"]),
                      "checks": [{"status": item["status"], "reason": item["reason"]} for item in preview["candidate_results"]]}, ensure_ascii=False))
    print("PASS: local interpretation + real DXF preview on isolated copy. No plan changes applied.")


if __name__ == "__main__":
    main()
