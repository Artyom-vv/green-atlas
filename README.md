# Green Atlas

Локальное приложение для проектирования озеленения по CAD-подоснове.

1. Откройте DWG/DXF и его подосновы в AutoCAD.
2. Запустите Green Atlas через плагин, проверьте слои и подготовьте карту.
3. Выберите участок, разместите растения и сохраните план.
4. Соберите результат с отдельным CAD-чертежом, ведомостью и 3D-сценой.

AutoCAD читает и восстанавливает геометрию. Green Atlas рассчитывает отступы
и размещение по сохранённому снимку. Для выпуска AutoCAD записывает посадки
в отдельную копию чертежа и проверяет её повторным открытием.
Распознавание слоёв поддерживает предложения модели и проверку человеком.

Проверенный пример — Кустанайская. Пропущенные объекты и недоступные подосновы
показываются в замечаниях. Поддержка всех улиц пока не подтверждена.

[Документация и схемы](docs/README.md)
[Состояние возможностей](<docs/obsidian/00 Обзор/Текущее состояние.md>)
[CAD-контракт](docs/architecture/autocad-contract.md)

## Состав

- `apps/web` — интерфейс и карта.
- `apps/api` — локальное хранение, геометрия, правила и посадки.
- `tools/autocad-bridge` — плагин AutoCAD и упаковка приложения.
- `packages/ui` — дизайн-система.
- `packages/api-client` — типы API и HTTP-клиент.
- `fixtures` — тестовые данные.
- `docs` — документация, архитектура и база знаний.

## Запуск для разработки

Нужны Node/pnpm из `package.json` и Python из `apps/api/.python-version`.
В Windows перед каждой командой применяйте `. ./scripts/windows-env.ps1`
в той же PowerShell-сессии.

Из корня репозитория:

```bash
pnpm install --frozen-lockfile
uv sync --project apps/api --locked --group dev
uv run --project apps/api --no-sync python apps/api/scripts/check_runtime.py
```

Запуск API из `apps/api`:

```bash
uv run --no-sync uvicorn app.main:app --reload --port 8000
```

Во втором терминале из корня:

```bash
pnpm dev:web
```

Интерфейс: `http://127.0.0.1:5173`, API: `http://127.0.0.1:8000`.
Этот запуск предназначен для разработки. Сборка плагина и desktop-приложения
описана в [инструкции CAD-моста](tools/autocad-bridge/README.md).
Новый проект открывается через AutoCAD, а не через старый DXF-upload.

## Проверки

```bash
python3 scripts/architecture/cad_dependencies.py --check
python3 scripts/docs/check_vault.py
uv run --project apps/api --group dev pytest apps/api/tests
pnpm typecheck
pnpm lint
pnpm test
pnpm build
pnpm check:api
```

`pnpm generate:api` обновляет контракт API. Сгенерированные типы вручную
не редактируются. Для сквозного CAD-теста нужны AutoCAD и совместимый плагин.

Аудиты, результаты прогонов и сборки хранятся локально.
[Правила содержимого репозитория](docs/repository-content.md).
