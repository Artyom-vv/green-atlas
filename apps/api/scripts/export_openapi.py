"""Экспорт HTTP-контракта OpenAPI 3.1 для генераторов клиента."""

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.main import app  # noqa: E402

parser = argparse.ArgumentParser(
    description="Экспорт контракта API без запуска HTTP-сервера"
)
parser.add_argument("--output", type=Path, default=API_ROOT / "openapi.json")
target = parser.parse_args().output
schema = app.openapi()
# FastAPI описывает элементы SSE через itemSchema из OpenAPI 3.2.
# Контракт 3.1 сохраняет эти сведения расширением, а ответ описывает как поток текста.
for path in schema["paths"].values():
    for operation in path.values():
        for response in operation.get("responses", {}).values():
            stream = response.get("content", {}).get("text/event-stream")
            if stream and "itemSchema" in stream:
                stream["x-item-schema"] = stream.pop("itemSchema")
                stream["schema"] = {"type": "string"}
target.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
print(target)
