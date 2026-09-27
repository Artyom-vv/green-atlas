"""Read-only live-model evaluation against the current project's real catalog/zones."""
import json
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent_interpreter import interpret_task, project_context
from app.agent_memory import TaskState
from app.contracts import Project


def main():
    with urlopen("http://127.0.0.1:8001/api/projects/0216cb9e-23ac-429e-bd1a-08815164ef0f?include_geometry=false", timeout=15) as response:
        project = Project.model_validate_json(response.read())
    context = project_context(project)
    state, records = TaskState(), []
    messages = ["Я хочу вдоль зданий на участке 6 деревья посадить штук 100, хз какой породы",
                "Ну, видимо, допустимая область 6", "сам выбери", "Я же просил вдоль зданий", "Дуб"]
    for index, text in enumerate(messages):
        patch, interpretation = interpret_task(text, state, records, context, "qwen3.5:4b")
        state = (TaskState() if interpretation.intent == "new_task" else state).amended(patch, str(index))
        records.append({"kind": "message", "payload": {"content": {"role": "user", "text": text}}})
        if interpretation.question:
            records.append({"kind": "message", "payload": {"content": {"role": "assistant", "text": interpretation.question}}})
        print(json.dumps({"message": text, "interpretation": interpretation.model_dump(), "task": state.model_dump()}, ensure_ascii=False), flush=True)
    assert state.values.quantity == 100, "Quantity lost"
    assert state.values.arrangement == "building_contour", "Arrangement lost"
    assert state.values.species_revision_ids == ["quercus-robur@2026-08-28.1"], "Oak not selected"
    expected = [zone["id"] for zone in context["zones"] if zone["label"] == "Допустимая область 6"]
    assert expected and state.values.zone_ids == expected, "Zone not retained"
    assert state.values.operation == "place", "Placement operation not identified"
    assert state.values.plant_kind == "tree", "Plant kind not identified"
    print("PASS: accumulated intent only; no geometry generation or plan mutation tested.", flush=True)


if __name__ == "__main__":
    main()
