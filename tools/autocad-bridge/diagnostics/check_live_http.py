"""Диагностика HTTP: быстрый статус читает API, старые пространственные пробы
явно выполняют проверку посадок и предпросмотр.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

STATUS_REQUEST_TIMEOUT_SECONDS = 2.0
STATUS_TOTAL_TIMEOUT_SECONDS = 5.0
STATUS_MAX_RESPONSE_BYTES = 256 * 1024
SESSION_COOKIE_NAME = "green_atlas_local_session"
SESSION_INPUT_BYTES = 129
OPERATION_FIELDS = (
    "id",
    "project_id",
    "kind",
    "status",
    "stage",
    "progress",
    "progress_mode",
    "processed_items",
    "total_items",
    "progress_unit",
    "project_state_version",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "error",
)
RECOGNITION_FIELDS = (
    "status",
    "stage",
    "processed_count",
    "total_count",
    "stage_processed_count",
    "stage_total_count",
    "active_layer_ids",
    "message",
    "updated_at",
    "error",
)


def status_target(url: str) -> tuple[str, str]:
    """Разрешён только известный проект уже работающего loopback API."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"/api/projects/[A-Za-z0-9_-]{1,128}", parsed.path)
    ):
        raise ValueError("Нужен loopback URL /api/projects/<id> с явным портом")
    return f"http://{parsed.netloc}", parsed.path


def session_from_stdin() -> str:
    """Одно доступное чтение pipe без ожидания ввода и записи launch-секрета."""
    descriptor = sys.stdin.fileno()
    blocking = os.get_blocking(descriptor)
    try:
        os.set_blocking(descriptor, False)
        value = os.read(descriptor, SESSION_INPUT_BYTES).decode("ascii").strip()
    finally:
        os.set_blocking(descriptor, blocking)
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", value):
        raise ValueError("Сессионная cookie не передана через stdin")
    return value


async def read_status(
    url: str,
    *,
    kind: str = "calculate_geometry",
    recognition: bool = False,
    session: str = "",
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    """Не более трёх GET с общим дедлайном, включая чтение тела ответа."""
    origin, project_path = status_target(url)
    started = time.monotonic()
    checks: dict[str, object] = {}
    targets = [
        ("health", "/api/health", None),
        ("operation", project_path + "/operations/latest", {"kind": kind}),
    ]
    if recognition:
        targets.append(
            ("recognition", project_path + "/source-layer-review/status", None)
        )
    async with httpx.AsyncClient(
        base_url=origin,
        timeout=STATUS_REQUEST_TIMEOUT_SECONDS,
        trust_env=False,
        follow_redirects=False,
        transport=transport,
        cookies={SESSION_COOKIE_NAME: session} if session else None,
    ) as client:
        for name, path, params in targets:
            remaining = STATUS_TOTAL_TIMEOUT_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                checks[name] = {"outcome": "deadline"}
                break
            request_started = time.monotonic()
            record: dict[str, object] = {}
            try:
                async with asyncio.timeout(
                    min(STATUS_REQUEST_TIMEOUT_SECONDS, remaining)
                ):
                    async with client.stream("GET", path, params=params) as response:
                        record["http_status"] = response.status_code
                        if response.status_code == 403:
                            record["outcome"] = "auth_required"
                        elif response.status_code != 200:
                            record["outcome"] = "http_error"
                        else:
                            body = bytearray()
                            async for chunk in response.aiter_bytes():
                                body.extend(chunk)
                                if len(body) > STATUS_MAX_RESPONSE_BYTES:
                                    raise ValueError("Ответ статуса слишком большой")
                            payload = json.loads(body)
                            if payload is None and name != "health":
                                record.update(outcome="not_found", data=None)
                            elif isinstance(payload, dict):
                                fields = (
                                    ("status",)
                                    if name == "health"
                                    else OPERATION_FIELDS
                                    if name == "operation"
                                    else RECOGNITION_FIELDS
                                )
                                data = {
                                    key: payload[key]
                                    for key in fields
                                    if key in payload
                                }
                                if session:
                                    data = json.loads(
                                        json.dumps(data).replace(session, "[hidden]")
                                    )
                                record.update(outcome="received", data=data)
                            else:
                                raise ValueError("Неожиданный ответ статуса")
            except (TimeoutError, httpx.TimeoutException):
                record["outcome"] = "timeout"
            except httpx.RequestError:
                record["outcome"] = "disconnected"
            except (ValueError, UnicodeError):
                record["outcome"] = "invalid_response"
            record["elapsed_s"] = round(time.monotonic() - request_started, 3)
            checks[name] = record
            if record.get("outcome") not in {"received", "not_found"}:
                break
    return {
        "mode": "status",
        "origin": origin,
        "project_id": project_path.rsplit("/", 1)[1],
        "checks": checks,
        "elapsed_s": round(time.monotonic() - started, 3),
    }


def status_exit_code(receipt: dict[str, object]) -> int:
    """Сбой чтения или явный отказ операции прерывает runner; активная работа — нет."""
    checks = receipt.get("checks")
    if not isinstance(checks, dict) or not checks:
        return 1
    for name, check in checks.items():
        if not isinstance(check, dict) or check.get("outcome") not in {
            "received",
            "not_found",
        }:
            return 1
        data = check.get("data")
        if isinstance(data, dict) and (
            data.get("status") == "failed"
            or (name == "health" and data.get("status") != "ok")
        ):
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--road-pattern", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--kind", default="calculate_geometry")
    parser.add_argument("--recognition", action="store_true")
    parser.add_argument("--session-stdin", action="store_true")
    args = parser.parse_args()
    if args.status:
        from app.operations.contracts import OperationKind

        try:
            kind = OperationKind(args.kind)
            status_target(args.url)
            session = session_from_stdin() if args.session_stdin else ""
        except (ValueError, OSError, UnicodeError):
            parser.error(
                "Проверьте loopback URL, вид операции и доступную stdin cookie"
            )
        if args.road_pattern:
            parser.error("--status не выполняет пространственные пробы")
        status_receipt = asyncio.run(
            read_status(
                args.url, kind=kind.value, recognition=args.recognition, session=session
            )
        )
        if args.receipt:
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(
                json.dumps(status_receipt, ensure_ascii=False, indent=2)
            )
        print(json.dumps(status_receipt, ensure_ascii=False))
        return status_exit_code(status_receipt)
    if not args.receipt or args.recognition or args.session_stdin:
        parser.error(
            "Для пространственных проб нужен --receipt; status-флаги требуют --status"
        )
    controls = {
        "closed_building": (15935.0, -4960.0),
        "road": (16092.001603, -5397.977498),
        "open_building_not_certified": (15994.606306, -5245.249309),
        "candidate_ground": (15970.0, -4920.0),
    }
    receipt = {"url": args.url, "controls": []}
    with httpx.Client(timeout=120) as client:
        project = client.get(args.url).raise_for_status().json()
        receipt["basis"] = {
            k: project[k] for k in ("id", "state_version", "geometry_version")
        }
        for name, (x, y) in controls.items():
            start = time.monotonic()
            response = client.post(
                args.url + "/plan/placement-check",
                json={
                    "kind": "shrub",
                    "x": x,
                    "y": y,
                    "radius": 0.5,
                    "base_plan_version": project["plan"]["version"],
                    "state_version": project["state_version"],
                    "geometry_version": project["geometry_version"],
                },
            )
            record = {
                "name": name,
                "elapsed_s": time.monotonic() - start,
                "http_status": response.status_code,
                "body": response.json(),
            }
            receipt["controls"].append(record)
            print(
                json.dumps(
                    {
                        "name": name,
                        "elapsed_s": record["elapsed_s"],
                        "http_status": response.status_code,
                        "code": record["body"].get("code"),
                        "allowed": record["body"].get("allowed"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        if args.road_pattern:
            request = {
                "type": "fill",
                "base_plan_version": project["plan"]["version"],
                "zone_ids": ["native-road-control"],
                "plant_kind": "shrub",
                "composition": "shrubs",
                "placement_mode": "count",
                "target_count": 5,
                "spacing_m": 2.15,
                "edge_offset_m": 0.65,
                "layout": "natural",
                "layout_radius_m": 0.65,
                "species_revision_id": "spiraea-japonica@2026-08-28.1",
                "seed": 1,
            }
            start = time.monotonic()
            response = client.post(args.url + "/plan/patterns/preview", json=request)
            response.raise_for_status()
            receipt["road_pattern"] = {
                "request": request,
                "reply": response.json(),
                "elapsed_s": time.monotonic() - start,
            }
            print(
                json.dumps(
                    {
                        "road_pattern": {
                            key: response.json()[key]
                            for key in (
                                "requested_count",
                                "generated_count",
                                "accepted_count",
                            )
                        }
                    }
                )
            )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
