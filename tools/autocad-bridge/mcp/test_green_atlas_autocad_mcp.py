from __future__ import annotations

import json

from green_atlas_autocad_mcp import TOOLS, _handle


def test_initialize_and_list_tools() -> None:
    initialized = _handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    assert initialized is not None
    assert initialized["result"]["serverInfo"]["name"] == "green-atlas-autocad"

    listed = _handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed is not None
    assert listed["result"]["tools"] == TOOLS


def test_stdio_payload_is_json_serializable() -> None:
    response = _handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    assert json.loads(json.dumps(response))["result"] == {}


def test_unknown_tool_is_a_tool_error() -> None:
    response = _handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "missing", "arguments": {}},
        }
    )
    assert response is not None
    assert response["result"]["isError"] is True
