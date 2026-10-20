# Docker: среда разработки

Dockerfile и Compose предназначены для отдельной проверки Web/API.
Это не установщик Windows 0.1.133 и не второй способ загрузки CAD.
Подготовка нового проекта требует AutoCAD и плагина по общему контракту.
Готовая Linux-поставка, поддержка МосТех.ОС и сквозной CAD-сценарий в контейнере
не квалифицированы.

## Запуск из корня

```sh
docker compose config --quiet
docker compose build
docker compose up -d
docker compose --profile test run --rm api-test
```

В Windows перед каждой командой: `. ./scripts/windows-env.ps1`.
Версии и digest образов закреплены в Dockerfile, зависимости — в lockfile.
Секреты, рабочие базы и датасеты не входят в build context.

Web доступен на `http://127.0.0.1:8080`, API — через `/api`, схема —
`/openapi.json`. Порт задаёт `GREEN_ATLAS_WEB_PORT`.
Том `project-data` сохраняет SQLite при обычном `docker compose down`.
Не подключайте рабочий профиль как тестовую базу.

Наличие конфигурации и её разбор не подтверждают сборку образов, запуск,
CAD или рендер. [Текущая установка](../../submission/prototype/README.md),
[CAD-контракт](../../docs/architecture/autocad-contract.md),
[архив прежних Docker-проверок](../../docs/archive/pre-133/deploy/docker/README.md).
