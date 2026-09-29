#!/usr/bin/env python3
"""Install per-user API credentials from a private env file, never print secrets."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))
from app.layer_recognition.api_settings import ApiSettings, settings_directory
from app.layer_recognition.budget import RecognitionBudget


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument(
        "--proxy-only",
        action="store_true",
        help="Update only the proxy of an existing configuration",
    )
    args = parser.parse_args()
    values = {}
    for line in args.env_file.read_text().splitlines():
        key, separator, value = line.strip().removeprefix("export ").partition("=")
        if separator and key in {"OPENAI_API_KEY", "OPENAI_PROXY_URL"}:
            values[key] = value.strip().strip("\"'")
    root = settings_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = root / "openai.json"
    if args.proxy_only:
        existing = json.loads(destination.read_text())
        settings = ApiSettings(
            key=existing["api_key"], proxy=values.get("OPENAI_PROXY_URL")
        )
        RecognitionBudget(settings.ledger).total()
        temporary = root / "openai.pending.json"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump({"api_key": settings.key, "proxy_url": settings.proxy}, output)
        temporary.replace(destination)
        print("Прокси обновлён. Ключ и общий расход сохранены.")
        return
    settings = ApiSettings(
        key=values.get("OPENAI_API_KEY", ""), proxy=values.get("OPENAI_PROXY_URL")
    )
    # Never replace existing configuration or reset an existing budget implicitly.
    if destination.exists():
        raise SystemExit(
            "Настройка уже существует. Секреты и бюджет оставлены без изменений"
        )
    RecognitionBudget(settings.ledger).initialize()
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump({"api_key": settings.key, "proxy_url": settings.proxy}, output)
    print(
        "Защищённая настройка API сохранена. Общий бюджет: $2; прежний расход сохранён."
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError):
        raise SystemExit(
            "Не удалось настроить API. Проверьте приватный env-файл и права доступа"
        ) from None
