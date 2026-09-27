# Локальная Docker-поставка

Подготовлены раздельные API и frontend images, Compose и тестовый API target.
Базовые образы закреплены digest, Python/Node соответствуют проверяемому
Windows runtime по версиям языка. Python использует `uv.lock`, frontend —
`pnpm-lock.yaml`; версия uv 0.12.11, pnpm 11.9.0. Установка uv соответствует
[официальной Docker-интеграции](https://docs.astral.sh/uv/guides/integration/docker/).
Digest разрешены через официальные registries 15 сентября 2026 года и хранятся
непосредственно в Dockerfile. Секреты, локальные базы, кеши и dataset не входят
в build context.

Из корня репозитория:

```sh
docker compose config --quiet
docker compose build
docker compose up -d
docker compose --profile test run --rm api-test
```

Интерфейс — `http://127.0.0.1:8080`, API доступен через `/api`, схема —
`/openapi.json`, Swagger UI — `/docs`. SQLite находится в
именованном томе `project-data`; обычный `docker compose down` его сохраняет.
API работает одним процессом от отдельного пользователя; локальная модель
выключена. Порт можно изменить через `GREEN_ATLAS_WEB_PORT`.

Проверка здоровья:

```sh
docker compose ps
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').read().decode())"
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/openapi.json').status)"
```

Основной frontend обслуживается Nginx, history routes возвращают `index.html`.
Статика с digest кешируется; JSON/JS/CSS сжимается. Reverse proxy сохраняет
лимит существующего release upload: 120 МиБ + multipart overhead. API входы
сами проверяют собственные ограничения.

**Проверено:** read-only `docker compose config --quiet` успешно разбирает
конфигурацию. **Не проверено:** build, запуск образов, Linux-тесты и работа на
МосТех.ОС. Автоматическая проверка отклонила запуск Docker Desktop с причиной
`blocked by policy`; демон оставался выключен. Наличие Dockerfile не закрывает
критерий приёмки ТЗ.

DWG-конвертер в базовый API image не включён. Его Linux-совместимость и лицензия
должны быть квалифицированы для полного CAD pipeline; Windows LibreDWG нельзя
копировать в Linux image. Server intake должен принимать отдельный read-only
mount набора и каталог результатов, с явно настроенным разрешённым корнем.
Эта интеграция добавляется после завершения контракта server intake.
