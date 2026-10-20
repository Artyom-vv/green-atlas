"""Native wizard transport. UTF-8 input, UTF-16 INI output for Windows APIs."""

import configparser
import io
from pathlib import Path

from app.desktop import dependencies
from app.shared.tool_settings import FIELDS


def wizard_action(action: str, incoming: Path | None, outgoing: Path) -> bool:
    if action == "discover":
        result = dependencies.discover()
    else:
        if incoming is None or incoming.stat().st_size > 65536:
            raise ValueError("Не переданы пути компонентов")
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(incoming.read_text(encoding="utf-8-sig"))
        values = {field: config.get("paths", field, fallback="") for field in FIELDS}
        result = (dependencies.save if action == "save" else dependencies.check)(values)
    output = configparser.ConfigParser(interpolation=None)
    output["paths"] = result["paths"]
    output["result"] = {
        "valid": "1" if result.get("valid", True) else "0",
        "warning": str(result.get("warning", "")),
    }
    for field, value in result["checks"].items():
        output["result"][field] = value["message"]
    text = io.StringIO()
    output.write(text)
    outgoing.write_text(text.getvalue(), encoding="utf-16")
    return bool(result.get("valid", True))
