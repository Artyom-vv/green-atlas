# Зелёный контур

Локальное рабочее пространство проектирования озеленения с входом через AutoCAD.

Целевой вход — открытый DWG/DXF в AutoCAD → плагин → локальный Green Atlas.
AutoCAD отвечает за интерпретацию CAD; отдельный импорт через ezdxf не развивается.
Файл может содержать другие названия слоёв, локальную систему координат,
неполную границу и смешанные типы CAD-геометрии.
ВДНХ используется только как крупный нагрузочный fixture для проверки производительности;
ни один пользовательский сценарий или контракт не привязан к этой территории.

Сквозной путь пока не завершён: GAOPEN и прямой native snapshot расходятся,
а выпуск AUTOCAD_LIVE не подключён. Расчёт на snapshot-геометрии существует,
но имеет ограничения допуска и полноты. Не считать unit-тесты обещанием
полного импорта любой улицы. [Действующий контракт и план](docs/architecture/autocad-contract.md),
[реестр нестыковок](docs/audits/2026-09-22-cad-pipeline.md).

## Состав

- `apps/web` — React-приложение и предметные UI-компоненты.
- `tools/autocad-bridge` — native-плагин, передача и упаковка локального приложения.
- `apps/api` — FastAPI, приём native snapshot, Shapely-расчёт; legacy DXF reader/writer пока сохранены для совместимости.
- `packages/ui` — независимая дизайн-система «Технический атлас».
- `packages/api-client` — OpenAPI-типы и HTTP-клиент.
- `fixtures` — тестовые данные сквозного сценария.
- `docs` — архитектура и правила зависимостей.

## Запуск web/API для разработки (не приёмка плагина)

```bash
pnpm install
cd apps/api
uv sync --locked --group dev
uv run --no-sync python scripts/check_runtime.py
uv run uvicorn app.main:app --reload --port 8000
```

API использует CPython из `apps/api/.python-version` (3.13.15) и готовые
C-ускорители ezdxf. `uv.lock` фиксирует зависимости; проверка перед запуском
останавливает неверное окружение или отключённые ускорители. Для Windows
сначала примените [окружение на диске проекта](docs/implementation/windows-local-runtime.md).

Во втором терминале из корня:

```bash
pnpm dev:web
```

Web-приложение ожидает API на `http://127.0.0.1:8000`. Откройте `http://127.0.0.1:5173/projects/new/import` и используйте `fixtures/site.dxf`. Для нагрузочной проверки есть `fixtures/large-map/vdnkh-large.dxf`; это только большой чертёж, а не преднастроенная территория или источник правил.

## Проверки

Граница CAD-зависимостей: `python3 scripts/architecture/cad_dependencies.py --check`.
Граф и clangd: [инструменты разработки](scripts/architecture/README.md).

```bash
cd apps/api && uv run --group dev pytest
pnpm typecheck
pnpm lint
pnpm test
pnpm build
pnpm storybook:build
pnpm test:e2e
pnpm test:e2e:headless
pnpm generate:api
```

`generate:api` экспортирует OpenAPI из FastAPI/Pydantic и заново создаёт `packages/api-client/src/schema.d.ts`. Сгенерированный файл не редактируется вручную.

Подробнее: [граница текущей сборки](docs/current-product-boundary.md), [архитектура](docs/architecture.md), [API-контракт](docs/api-contract.md), [правила модулей](docs/module-rules.md).
