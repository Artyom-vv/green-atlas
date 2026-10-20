#!/usr/bin/env python3
"""Store a relay access token outside source, project data and installer."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))
from app.layer_recognition.api_settings import settings_directory
from app.layer_recognition.relay_settings import RelaySettings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    args = parser.parse_args()
    token = args.token_file.read_text().strip()
    settings = RelaySettings(args.url, token)
    root = settings_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = root / "relay.json"
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump({"url": settings.url, "token": settings.token}, output)
    print("Доступ к серверу распознавания настроен для этого пользователя")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError):
        raise SystemExit(
            "Не удалось настроить сервер распознавания. Проверьте URL и файл токена"
        ) from None
