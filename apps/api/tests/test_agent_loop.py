import copy
import json

from app.agent_loop import PlacementStep, ReadStep, run_placement_agent, run_read_agent, choose_read_step
from app.agent_memory import TaskPatch, TaskState
from app import planning_assistant as local
from test_agent_planning import existing_application


def step(action="tool", tool="project_context", arguments="{}", text="", evidence=None):
    return ReadStep(action=action, tool=tool, arguments_json=arguments, text=text, evidence=evidence or [])


def placement_step(action="tool", tool="project_context", arguments="{}", text="", evidence=None):
    return PlacementStep(action=action, tool=tool, arguments_json=arguments, text=text, evidence=evidence or [])


def test_placement_agent_selects_real_tools_then_returns_preview_without_mutation(monkeypatch):
    from app import agent_loop
    domain, project = existing_application()
    task = TaskState().amended(TaskPatch(
        operation="place", scope="zones", zone_ids=["west"], plant_kind="tree",
        quantity=10, quantity_mode="target", arrangement="building_contour",
        species_mode="automatic"), "user")
    prepared = {"task": task.model_dump(mode="json"), "change_set": {"can_apply": True},
                "requested": 10, "found": 10, "shortfall": 0}
    monkeypatch.setattr(agent_loop, "prepare_agent_task", lambda *_, **__: prepared.copy())
    calls = iter([
        placement_step(tool="project_context"),
        placement_step(tool="inspect_zones", arguments='{"zone_ids":["west"]}'),
        placement_step(tool="building_targets", arguments='{"zone_ids":["west"]}'),
        placement_step(tool="species_shortlist", arguments='{"kind":"tree"}'),
        placement_step(tool="prepare_placement"),
        placement_step(action="finish", tool=""),
    ])
    contexts = []

    def choose(_, context):
        contexts.append(copy.deepcopy(context))
        return next(calls)

    before = domain.get(project.id).model_dump_json()
    result = run_placement_agent(domain, project.id, task, "test", choose=choose)
    assert result["change_set"]["can_apply"]
    assert result["agent_trace"]["mode"] == "local_agent"
    assert [item["tool"] for item in result["agent_trace"]["events"]] == [
        "project_context", "inspect_zones", "building_targets", "species_shortlist", "prepare_placement"]
    assert contexts[3]["events"][-1]["tool"] == "building_targets"
    assert contexts[4]["events"][-1]["tool"] == "species_shortlist"
    assert contexts[5]["events"][-1]["tool"] == "prepare_placement"
    assert domain.get(project.id).model_dump_json() == before


def test_placement_model_request_is_bounded_and_uses_structured_transport(monkeypatch):
    from app import agent_loop
    requests = []

    def respond(_, body, **kwargs):
        requests.append(body)
        return {"message": {"content": placement_step().model_dump_json()}}

    monkeypatch.setattr(agent_loop.local, "local_json", respond)
    agent_loop.choose_placement_step("test", {"task": {}, "events": [], "tools": []})
    assert requests[-1]["think"] is True
    assert requests[-1]["format"]["properties"]["evidence"]["maxItems"] == 8
    assert requests[-1]["options"]["num_predict"] == 1200


def test_delegated_zone_is_required_before_inspection_and_scope_is_pinned():
    from app import agent_loop
    task = TaskState().amended(TaskPatch(
        operation="place", scope="zones", zone_ids=[], spatial_anchor="edge",
        alignment_target="road", plant_kind="tree", quantity_mode="fill_available",
        arrangement="road_edges", species_mode="automatic", post_action="focus_map",
    ), "user")
    assert agent_loop._placement_required_tools(task) == [
        "project_context", "select_zone_by_spatial_intent", "inspect_zones",
        "road_targets", "species_shortlist:tree",
    ]
    selected = agent_loop._placement_event({
        "tool": "select_zone_by_spatial_intent", "effect": "read",
        "result": {"zone_id": "west", "label": "West", "edge_contact_m": 12.5,
                   "alignment_target": "road", "road_count": 1},
    })
    assert agent_loop._placement_arguments(task, "inspect_zones", "{}", [selected]) == {"zone_ids": ["west"]}
    assert agent_loop._placement_arguments(task, "road_targets", "{}", [selected]) == {"zone_ids": ["west"]}


def test_agent_species_selection_is_validated_and_reaches_preview(monkeypatch):
    from app import agent_loop
    domain, project = existing_application()
    task = TaskState().amended(TaskPatch(
        operation="place", scope="zones", zone_ids=["west"], plant_kind="mixed",
        quantity=10, quantity_mode="target", arrangement="building_contour",
        species_mode="automatic"), "user")
    prepared = {"task": {}, "change_set": {"can_apply": True},
                "requested": 10, "found": 10, "shortfall": 0}
    captured = {}

    def fake_prepare(_, __, prepared_task, **kwargs):
        captured["task"] = prepared_task
        captured["agent_species_revision_ids"] = kwargs.get("agent_species_revision_ids")
        return prepared.copy()

    monkeypatch.setattr(agent_loop, "prepare_agent_task", fake_prepare)
    species = {
        "tree": {"id": "tree-1", "kind": "tree"},
        "shrub": {"id": "shrub-1", "kind": "shrub"},
    }

    def fake_execute(app, project_id, name, arguments):
        result = {"tree": [], "shrub": []}
        if name == "species_shortlist":
            result = [{"status": "available", "species": species[arguments["kind"]]}]
        return {"tool": name, "effect": "read", "project_id": project_id,
                "state_version": project.state_version, "result": result}

    monkeypatch.setattr(agent_loop, "execute_tool", fake_execute)
    calls = iter([
        placement_step(tool="project_context"),
        placement_step(tool="inspect_zones", arguments='{"zone_ids":["west"]}'),
        placement_step(tool="building_targets", arguments='{"zone_ids":["west"]}'),
        placement_step(tool="species_shortlist", arguments='{"kind":"tree"}'),
        placement_step(tool="species_shortlist", arguments='{"kind":"shrub"}'),
        placement_step(action="finish", tool="prepare_placement",
                       arguments='{"species_shortlist":{"tree":["tree-1"],"shrub":["shrub-1"]}}'),
        placement_step(action="finish", tool=""),
    ])
    result = run_placement_agent(domain, project.id, task, "test", choose=lambda *_: next(calls))
    assert captured["task"].values.species_mode == "automatic"
    assert captured["task"].values.species_revision_ids is None
    assert captured["agent_species_revision_ids"] == ["tree-1", "shrub-1"]
    assert result["agent_trace"]["selected_species_revision_ids"] == ["tree-1", "shrub-1"]
    assert result["task"] == task.model_dump(mode="json")


def test_agent_species_selection_accepts_kind_specific_model_keys(monkeypatch):
    from app import agent_loop
    step_value = placement_step(action="finish", tool="prepare_placement", arguments=json.dumps({
        "tree_species_ids": ["tree-1"],
        "shrub_species_ids": [{"id": "shrub-1", "common_name": "Тестовый кустарник"}],
    }))
    assert agent_loop._placement_species_from_step(
        step_value, {"tree-1": "tree", "shrub-1": "shrub"}
    ) == ["tree-1", "shrub-1"]


def test_prepare_context_without_species_uses_verified_shortlist(monkeypatch):
    from app import agent_loop
    domain, project = existing_application()
    task = TaskState().amended(TaskPatch(
        operation="place", scope="zones", zone_ids=["west"], plant_kind="tree",
        quantity=10, quantity_mode="target", arrangement="building_contour",
        species_mode="automatic"), "user")
    prepared = {"task": task.model_dump(mode="json"),
                "change_set": {"can_apply": True}, "requested": 10,
                "found": 10, "shortfall": 0,
                "species_revision_id": "tree-1"}
    captured = []

    def fake_prepare(_, __, prepared_task, agent_species_revision_ids=None):
        captured.append(agent_species_revision_ids)
        return prepared.copy()

    monkeypatch.setattr(agent_loop, "prepare_agent_task", fake_prepare)

    def fake_execute(_, __, name, arguments):
        if name == "species_shortlist":
            result = [{"status": "available", "species": {"id": "tree-1", "kind": "tree"}}]
        else:
            result = []
        return {"tool": name, "effect": "read", "project_id": project.id,
                "state_version": project.state_version, "result": result}

    monkeypatch.setattr(agent_loop, "execute_tool", fake_execute)
    calls = iter([
        placement_step(tool="project_context"),
        placement_step(tool="inspect_zones", arguments='{"zone_ids":["west"]}'),
        placement_step(tool="building_targets", arguments='{"zone_ids":["west"]}'),
        placement_step(tool="species_shortlist", arguments='{"kind":"tree"}'),
        placement_step(action="finish", tool="prepare_placement",
                       arguments='{"zone_ids":["west"],"quantity":10,"plant_kind":"tree","species_mode":"automatic"}'),
        placement_step(action="finish", tool=""),
    ])
    result = agent_loop.run_placement_agent(domain, project.id, task, "test", choose=lambda *_: next(calls))
    assert captured == [["tree-1"]]
    assert result["agent_trace"]["selected_species_revision_ids"] == ["tree-1"]


def test_placement_agent_allows_one_species_correction_after_shortfall(monkeypatch):
    from app import agent_loop
    domain, project = existing_application()
    task = TaskState().amended(TaskPatch(
        operation="place", scope="zones", zone_ids=["west"], plant_kind="mixed",
        quantity=10, quantity_mode="target", arrangement="building_groves",
        species_mode="automatic"), "user")
    previews = iter([
        {"task": task.model_dump(mode="json"), "species_revision_ids": ["tree-1", "shrub-1"],
         "change_set": {"can_apply": True}, "requested": 10, "found": 7, "shortfall": 3},
        {"task": task.model_dump(mode="json"), "species_revision_ids": ["tree-2", "shrub-2"],
         "change_set": {"can_apply": True}, "requested": 10, "found": 10, "shortfall": 0},
    ])
    selected = []

    def fake_prepare(_, __, prepared_task, agent_species_revision_ids=None):
        selected.append((prepared_task, agent_species_revision_ids))
        return next(previews).copy()

    monkeypatch.setattr(agent_loop, "prepare_agent_task", fake_prepare)

    def fake_execute(_, __, name, arguments):
        if name == "species_shortlist":
            kind = arguments["kind"]
            ids = [f"{kind}-1", f"{kind}-2"]
            result = [{"status": "available", "species": {"id": identity, "kind": kind}}
                      for identity in ids]
        else:
            result = []
        return {"tool": name, "effect": "read", "project_id": project.id,
                "state_version": project.state_version, "result": result}

    monkeypatch.setattr(agent_loop, "execute_tool", fake_execute)
    calls = iter([
        placement_step(tool="project_context"),
        placement_step(tool="inspect_zones", arguments='{"zone_ids":["west"]}'),
        placement_step(tool="building_targets", arguments='{"zone_ids":["west"]}'),
        placement_step(tool="species_shortlist", arguments='{"kind":"tree"}'),
        placement_step(tool="species_shortlist", arguments='{"kind":"shrub"}'),
        placement_step(action="finish", tool="prepare_placement",
                       arguments='{"species_revision_ids":["tree-1","shrub-1"]}'),
        placement_step(action="finish", tool="prepare_placement",
                       arguments='{"species_revision_ids":["tree-2","shrub-2"]}'),
        placement_step(action="finish", tool=""),
    ])
    result = run_placement_agent(domain, project.id, task, "test", choose=lambda *_: next(calls))
    assert [item[1] for item in selected] == [["tree-1", "shrub-1"], ["tree-2", "shrub-2"]]
    assert result["found"] == 10 and result["shortfall"] == 0
    assert len([event for event in result["agent_trace"]["events"] if event["tool"] == "prepare_placement"]) == 2
    assert result["agent_trace"]["selected_species_revision_ids"] == ["tree-2", "shrub-2"]


def test_placement_agent_keeps_best_preview_when_recursive_retry_is_worse(monkeypatch):
    from app import agent_loop
    domain, project = existing_application()
    task = TaskState().amended(TaskPatch(
        operation="place", scope="zones", zone_ids=["west"], plant_kind="mixed",
        quantity=10, quantity_mode="target", arrangement="building_groves",
        species_mode="automatic"), "user")
    previews = iter([
        {"task": task.model_dump(mode="json"), "species_revision_ids": ["tree-1", "shrub-1"],
         "change_set": {"can_apply": True}, "requested": 10, "found": 10, "shortfall": 0},
        {"task": task.model_dump(mode="json"), "species_revision_ids": ["tree-2", "shrub-2"],
         "change_set": {"can_apply": True}, "requested": 10, "found": 4, "shortfall": 6},
    ])

    def fake_prepare(_, __, prepared_task, agent_species_revision_ids=None):
        return next(previews).copy()

    monkeypatch.setattr(agent_loop, "prepare_agent_task", fake_prepare)

    def fake_execute(_, __, name, arguments):
        if name == "species_shortlist":
            kind = arguments["kind"]
            result = [{"status": "available", "species": {"id": f"{kind}-1", "kind": kind}},
                      {"status": "available", "species": {"id": f"{kind}-2", "kind": kind}}]
        else:
            result = []
        return {"tool": name, "effect": "read", "project_id": project.id,
                "state_version": project.state_version, "result": result}

    monkeypatch.setattr(agent_loop, "execute_tool", fake_execute)
    calls = iter([
        placement_step(tool="project_context"),
        placement_step(tool="inspect_zones", arguments='{"zone_ids":["west"]}'),
        placement_step(tool="building_targets", arguments='{"zone_ids":["west"]}'),
        placement_step(tool="species_shortlist", arguments='{"kind":"tree"}'),
        placement_step(tool="species_shortlist", arguments='{"kind":"shrub"}'),
        placement_step(action="finish", tool="prepare_placement",
                       arguments='{"species_revision_ids":["tree-1","shrub-1"]}'),
        placement_step(action="finish", tool="prepare_placement",
                       arguments='{"species_revision_ids":["tree-2","shrub-2"]}'),
        placement_step(action="finish", tool=""),
    ])
    result = run_placement_agent(domain, project.id, task, "test", choose=lambda *_: next(calls))
    assert result["found"] == 10
    assert result["agent_trace"]["selected_species_revision_ids"] == ["tree-1", "shrub-1"]


def test_placement_agent_is_not_scripted_to_eight_decisions(monkeypatch):
    from app import agent_loop
    domain, project = existing_application()
    task = TaskState().amended(TaskPatch(
        operation="place", scope="zones", zone_ids=["west"], plant_kind="tree",
        quantity=10, quantity_mode="target", arrangement="building_contour",
        species_mode="automatic"), "user")
    prepared = {"task": task.model_dump(mode="json"), "change_set": {"can_apply": True},
                "requested": 10, "found": 10, "shortfall": 0, "species_revision_id": "tree-1"}
    monkeypatch.setattr(agent_loop, "prepare_agent_task", lambda *_, **__: prepared.copy())

    def fake_execute(_, __, name, arguments):
        result = [{"status": "available", "species": {"id": "tree-1", "kind": "tree"}}] if name == "species_shortlist" else []
        return {"tool": name, "effect": "read", "project_id": project.id,
                "state_version": project.state_version, "result": result}

    monkeypatch.setattr(agent_loop, "execute_tool", fake_execute)
    calls = iter([
        placement_step(action="finish", tool=""),
        placement_step(action="finish", tool=""),
        placement_step(action="finish", tool=""),
        placement_step(tool="project_context"),
        placement_step(tool="inspect_zones", arguments='{"zone_ids":["west"]}'),
        placement_step(tool="building_targets", arguments='{"zone_ids":["west"]}'),
        placement_step(tool="species_shortlist", arguments='{"kind":"tree"}'),
        placement_step(action="finish", tool="prepare_placement"),
        placement_step(action="finish", tool=""),
    ])
    result = run_placement_agent(domain, project.id, task, "test", choose=lambda *_: next(calls))
    assert result["found"] == 10
    assert len(result["agent_trace"]["decisions"]) == 9
    assert not any(item.get("action") == "fallback" for item in result["agent_trace"]["decisions"])


def test_reads_real_tools_and_answers_with_evidence_without_mutation():
    domain, project = existing_application()
    before = domain.get(project.id).model_dump_json()
    calls = iter([step(), step(tool="find_plantings", arguments='{"limit":1}'),
                  step(action="answer", text="Найдены посадки. Показана первая запись.", evidence=[1])])
    contexts = []

    def choose(_, context):
        contexts.append(context)
        return next(calls)

    result = run_read_agent(domain, project.id, "Какие посадки есть?", "test", choose=choose)
    assert result.status == "answered"
    assert result.events[1]["data"]["result"]["next_offset"] == 1
    assert all(item["effect"] == "read" for item in contexts[0]["tools"])
    assert domain.get(project.id).model_dump_json() == before


def test_invalid_arguments_can_be_recovered_but_previews_are_not_executed():
    domain, project = existing_application()
    before = domain.get(project.id).model_dump_json()
    calls = iter([step(tool="preview_changes"), step(tool="find_plantings", arguments='{"project_id":"other"}'),
                  step(tool="find_plantings"), step(action="answer", text="Посадки проверены.", evidence=[2])])
    result = run_read_agent(domain, project.id, "Проверьте", "test", choose=lambda *_: next(calls))
    assert result.status == "answered"
    assert [event["ok"] for event in result.events] == [False, False, True]
    assert domain.get(project.id).model_dump_json() == before


def test_unsupported_answer_and_duplicate_calls_cannot_loop_forever():
    domain, project = existing_application()
    calls = iter([step(action="answer", text="Есть 100 деревьев", evidence=[0]), step(), step()])
    result = run_read_agent(domain, project.id, "Сколько?", "test", max_steps=3, choose=lambda *_: next(calls))
    assert result.status == "limit"
    assert [event["ok"] for event in result.events] == [False, True, False]


def test_project_change_during_model_call_invalidates_answer():
    domain, project = existing_application()

    def choose(*_):
        project.name = "Changed"
        domain.repository.save(project)
        return step()

    result = run_read_agent(domain, project.id, "Проверьте", "test", choose=choose)
    assert result.status == "stale"
    assert result.events == []


def test_stop_after_model_response_prevents_tool_execution():
    domain, project = existing_application()
    stop = False

    def choose(*_):
        nonlocal stop
        stop = True
        return step()

    result = run_read_agent(domain, project.id, "Проверьте", "test", choose=choose, stopped=lambda: stop)
    assert result.status == "stopped"
    assert result.events == []


def test_local_grammar_requires_observation_before_answer_and_uses_explicit_evidence_indices(monkeypatch):
    requests = []

    def respond(_, body, **kwargs):
        requests.append(body)
        return {"message": {"content": step().model_dump_json()}}

    monkeypatch.setattr(local, "local_json", respond)
    context = {"events": [], "tools": [{"name": "project_context"}]}
    choose_read_step("test", context)
    assert requests[-1]["format"]["properties"]["action"]["enum"] == ["tool"]
    context["events"] = [{"index": 0, "ok": False}, {"index": 1, "ok": True}]
    choose_read_step("test", context)
    assert "answer" in requests[-1]["format"]["properties"]["action"]["enum"]
    assert requests[-1]["format"]["properties"]["evidence"]["items"]["enum"] == [1]


def test_loop_can_read_beyond_original_large_result_limit(monkeypatch):
    from app import agent_loop
    domain, project = existing_application()
    monkeypatch.setattr(agent_loop, "execute_tool", lambda *_: {
        "state_version": project.state_version, "result": {"items": [{"id": str(i), "detail": "x" * 100} for i in range(1000)]}})
    calls = iter([step(), step(tool="read_result_page", arguments='{"event_index":0,"path":["result","items",999]}'),
                  step(action="answer", text="Проверен объект 999.", evidence=[1])])
    result = run_read_agent(domain, project.id, "Последний объект", "test", choose=lambda *_: next(calls))
    assert result.status == "answered"
    assert result.events[0]["data"]["truncated"] is True
    assert "excerpt" not in result.events[0]["data"]
    assert result.events[1]["data"]["items"][0] == {"key": "id", "value": "999"}


def test_growth_answer_cannot_invent_dimensions_when_forecasts_are_missing():
    domain, project = existing_application()
    calls = iter([step(tool="growth_objects", arguments='{"horizon_year":20,"object_ids":["one"]}'),
                  step(action="answer", text="Крона будет точно 99 метров.", evidence=[0])])
    result = run_read_agent(domain, project.id, "Прогноз", "test", choose=lambda *_: next(calls))
    assert result.status == "answered"
    assert "99" not in result.text
    assert "нет прогноза" in result.text
