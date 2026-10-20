"""Быстрая HTTP-диагностика: только GET, реальные дедлайны и закрытый секрет
локального сеанса. CAD, база и модель в этих проверках не запускаются.
"""

import asyncio
import json
import os
import time

import check_live_http as diagnostic
import httpx
import pytest

URL = "http://127.0.0.1:15432/api/projects/project-1"
SESSION = "s" * 43
OPERATION = {
    "id": "operation-1",
    "project_id": "project-1",
    "kind": "calculate_geometry",
    "status": "running",
    "stage": "Проверяем контуры",
    "processed_items": 15,
    "total_items": 30,
    "updated_at": "2026-10-09T16:00:00Z",
}


def status(handler, **options):
    return asyncio.run(
        diagnostic.read_status(URL, transport=httpx.MockTransport(handler), **options)
    )


def cli_exit_code(result, monkeypatch, capsys):
    async def saved_status(*args, **kwargs):
        return result

    monkeypatch.setattr(diagnostic, "read_status", saved_status)
    monkeypatch.setattr(
        diagnostic.sys, "argv", ["check_live_http.py", "--status", "--url", URL]
    )
    code = diagnostic.main()
    assert json.loads(capsys.readouterr().out) == result
    return code


def test_running_is_reported_without_claiming_hang():
    calls = []

    def respond(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["cookie"] == f"green_atlas_local_session={SESSION}"
        return httpx.Response(
            200,
            json={"status": "ok"} if request.url.path == "/api/health" else OPERATION,
        )

    result = status(respond, session=SESSION)
    assert len(calls) == 2
    assert calls[1].url.params["kind"] == "calculate_geometry"
    assert result["checks"]["operation"]["data"] == OPERATION
    assert "hang" not in json.dumps(result)
    assert SESSION not in json.dumps(result)
    assert diagnostic.status_exit_code(result) == 0


def test_completed_operation_is_reported_without_a_new_command(monkeypatch, capsys):
    calls = []

    def respond(request):
        calls.append(request.method)
        return httpx.Response(
            200,
            json={"status": "ok"}
            if request.url.path == "/api/health"
            else {**OPERATION, "status": "completed", "progress": 100},
        )

    result = status(respond)
    assert result["checks"]["operation"]["data"]["status"] == "completed"
    assert calls == ["GET", "GET"]
    assert cli_exit_code(result, monkeypatch, capsys) == 0


def test_failed_operation_keeps_error_and_redacts_reflected_cookie(monkeypatch, capsys):
    def respond(request):
        return httpx.Response(
            200,
            json={"status": "ok"}
            if request.url.path == "/api/health"
            else {
                **OPERATION,
                "status": "failed",
                "error": {
                    "code": "CAD_REFUSED",
                    "message": f"Подготовка остановлена {SESSION}",
                },
            },
        )

    result = status(respond, session=SESSION)
    assert result["checks"]["operation"]["data"]["error"] == {
        "code": "CAD_REFUSED",
        "message": "Подготовка остановлена [hidden]",
    }
    assert SESSION not in json.dumps(result)
    assert cli_exit_code(result, monkeypatch, capsys) == 1


def test_auth_failure_does_not_log_body_or_wait_for_stdin(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            403, json={"session_token": SESSION, "code": "LOCAL_SESSION_REQUIRED"}
        )

    result = status(respond)
    assert len(calls) == 1
    assert result["checks"]["health"]["outcome"] == "auth_required"
    assert SESSION not in json.dumps(result)
    assert diagnostic.status_exit_code(result) == 1
    reader, writer = os.pipe()
    with os.fdopen(reader) as source:
        monkeypatch.setattr(diagnostic.sys, "stdin", source)
        try:
            began = time.monotonic()
            with pytest.raises(BlockingIOError):
                diagnostic.session_from_stdin()
            assert time.monotonic() - began < 0.1
            os.write(writer, (SESSION + "\n").encode())
            assert diagnostic.session_from_stdin() == SESSION
            assert os.get_blocking(reader)
        finally:
            os.close(writer)


def test_disconnect_reports_unknown_without_retry():
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        raise httpx.ReadError(f"Connection lost {SESSION}")

    result = status(respond, session=SESSION)
    assert len(calls) == 2
    assert result["checks"]["operation"]["outcome"] == "disconnected"
    assert SESSION not in json.dumps(result)
    assert diagnostic.status_exit_code(result) == 1


def test_wall_time_includes_slow_body_and_total_remaining_budget(monkeypatch):
    monkeypatch.setattr(diagnostic, "STATUS_REQUEST_TIMEOUT_SECONDS", 0.04)
    monkeypatch.setattr(diagnostic, "STATUS_TOTAL_TIMEOUT_SECONDS", 0.06)

    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b" "
                await asyncio.sleep(0.005)

    def slow_body(request):
        return httpx.Response(200, stream=Trickle())

    began = time.monotonic()
    result = status(slow_body)
    assert result["checks"]["health"]["outcome"] == "timeout"
    assert diagnostic.status_exit_code(result) == 1
    assert time.monotonic() - began < 0.12
    calls = []

    async def slow_headers(request):
        calls.append(request.method)
        await asyncio.sleep(0.025)
        return httpx.Response(
            200,
            json={"status": "ok"} if request.url.path == "/api/health" else OPERATION,
        )

    began = time.monotonic()
    result = status(slow_headers, recognition=True)
    assert calls == ["GET", "GET", "GET"]
    assert result["checks"]["recognition"]["outcome"] == "timeout"
    assert diagnostic.status_exit_code(result) == 1
    assert time.monotonic() - began < 0.12


def test_external_targets_and_redirects_are_refused():
    for url in [
        "http://example.com:15432/api/projects/p",
        "http://127.0.0.1/api/projects/p",
        "http://secret@127.0.0.1:15432/api/projects/p",
        URL + "?token=x",
        "http://127.0.0.1:15432/api/projects",
    ]:
        with pytest.raises(ValueError):
            diagnostic.status_target(url)
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://example.com"})

    result = status(redirect)
    assert len(calls) == 1
    assert result["checks"]["health"]["http_status"] == 302
    assert result["checks"]["health"]["outcome"] == "http_error"
    assert diagnostic.status_exit_code(result) == 1
    invalid = status(lambda request: httpx.Response(200, content="not JSON"))
    assert invalid["checks"]["health"]["outcome"] == "invalid_response"
    assert diagnostic.status_exit_code(invalid) == 1


def test_optional_recognition_reads_status_when_no_geometry_operation_exists():
    calls = []

    def respond(request):
        calls.append(request)
        payload: dict[str, object]
        if request.url.path == "/api/health":
            payload = {"status": "ok"}
        elif request.url.path.endswith("/latest"):
            return httpx.Response(200, content="null")
        else:
            payload = {
                "status": "running",
                "stage": "reviewing",
                "processed_count": 124,
                "stage_processed_count": 3,
                "stage_total_count": 8,
                "updated_at": "2026-10-09T16:00:00Z",
                "message": "Уточняем спорные назначения",
                "proposals": [{"private_source": "not printed"}],
            }
        return httpx.Response(200, json=payload)

    result = status(respond, recognition=True, kind="prepare_cad_capture")
    assert len(calls) == 3
    assert all(request.method == "GET" for request in calls)
    assert calls[-1].url.path.endswith("/source-layer-review/status")
    assert result["checks"]["operation"]["data"] is None
    assert result["checks"]["recognition"]["data"]["stage_processed_count"] == 3
    assert "private_source" not in json.dumps(result)
    assert diagnostic.status_exit_code(result) == 0
