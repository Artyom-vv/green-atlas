# Backend: проверенная архитектура после рефакторинга 14 сентября 2026

Фасад `app/application.py` сохраняет публичные методы и конструктор существующих
потребителей. Его методы делегируют предметным сценариям; расчёт, кэши и запись
больше не принадлежат фасаду. Ресурсы SQLite создаёт `app/composition.py`, HTTP
получает приложение через `get_application`. Импорт `app.main` и экспорт OpenAPI
не создают базу данных.

## Принадлежность сценариев

| Сценарий | Владелец | Зависимости и граница |
| --- | --- | --- |
| Создание, список, паспорт, удаление проекта | `projects/application.py` | Каталог проектов, сброс истории, отмена активного расчёта; без файлов DXF и реализации SQLite |
| Чтение DXF/ZIP, назначение слоёв | `dxf_import/application.py` | Reader, сохранение проекта, сброс истории, инвалидация после успешной записи |
| Подготовка геометрии, прогресс, отмена | `operations/application.py` | Репозитории снимков и операций, geometry port, валидация, общий commit-lock |
| Запрос карты и индекс расстояний | `geometry/queries.py` | Reader, query/validator ports; единственный ограниченный кэш индекса плана |
| Проверка кандидата | `planning/evaluation.py` | Снимок проекта, текущий план и geometry port; без чтения репозитория |
| Preview/apply/receipt набора изменений | `planning/changes.py` | Единственный владелец кэшей preview/result, версий, digest и применения |
| Одиночные добавление, изменение, удаление | `planning/manual_application.py` | Узкий preview/apply port; единый путь применения трёх команд |
| Ряд/заполнение, кисть, рекомендации | `planning/*_application.py` | Reader, генератор кандидатов, общая оценка и preview того же снимка |
| Участки | `planting_zones/application.py` | Валидация и запись участков, receipts, создание ручного плана |
| Предложения изменения участков | `planting_zone_changes.py` | Составленный один раз сервис с узким `ZoneCommands`; совместимый getter не создаёт новый сервис |
| Undo/Redo и атомарная история | `history/application.py` | Snapshot repository и history port; рабочая геометрия не копируется в историю правок |
| Актуализация проверок | `validation/application.py` | Validator port и идентичность снимка; без HTTP |
| Каталог и подбор пород | `species/application.py` | Снимок и действующий каталог; расчётные правила не менялись |
| 3D-сцена | `scene/application.py`, `domain.py`, `context.py`, `plants.py` | Сборка из переданного снимка, подтверждённые источники высот, справочник пород |
| DXF и пакет выпуска | `exporting/application.py` | Узкий порт публикации, writer и функция сцены; существующая атомарность публикации |

## Состояние и конкурентные действия

- `Runtime` владеет репозиториями, лениво созданными conversation/run stores и их
  закрытием. История SQLite использует соединение репозитория проекта.
- Один manual edit-lock разделяют одиночные команды, change sets, история и
  участки. Отдельный operation commit-lock разделяют удаление проекта и запись
  фонового расчёта. Блокировки не заменяют compare-and-swap в SQLite.
- Предпросмотр ряда, кисти, рекомендации и одиночной команды получает тот же
  снимок, от которого взята версия. При применении сравниваются актуальные
  `state_version`, `geometry_version`, `plan.version`, digest и сроки preview.
- Инвалидация карты, расстояний и истории происходит после успешной записи.
  Отклонённый конфликт версий не удаляет состояние сохранившегося проекта.
- Временные идентификаторы и часы переданы в контроллеры явно. Математические
  функции в `planning/rules.py` и сборка сцены не обращаются к сети или БД.
- Agent background job захватывает приложение и store при запуске. Workflow
  возвращает `WorkflowConflict`; HTTP переводит его в прежний ответ 409.

## Контракты и совместимость

Wire-модели принадлежат предметным `contracts.py`, `change_contracts.py`,
`pattern_contracts.py` и `recommendation_contracts.py`. `app/contracts.py`
содержит только совместимые экспорты. `app/planting_zone_contracts.py` также
сохраняет публичные импорты; реализация находится в
`planting_zones/change_contracts.py`. Предметные контракты не импортируют общий
фасад. Новые сервисы и порты импортируют типы владельцев напрямую.

Имена Pydantic-моделей, defaults, JSON-поля, HTTP-схемы и сериализация сохранены.
Generated TypeScript продолжает создаваться из того же OpenAPI. Никакие новые
нормативы, алгоритмы расчёта, лимиты большого DXF или таблицы БД не вводились.

## Применённые принципы и границы абстракций

| Проблема | Решение | Проверка |
| --- | --- | --- |
| HTTP создавал общий граф и его импортировал агент | DIP/SRP: ресурсы в composition root, сценарии получают порты | Импорт OpenAPI без создания БД, независимые runtimes, отсутствие HTTP-импортов в workflows |
| Один фасад владел несовместимыми жизненными циклами | SRP/ISP: сценарии и кэши имеют отдельных владельцев | Архитектурные тесты зависимостей, профильные и интеграционные тесты |
| Генерация повторно читала проект в середине preview | Функциональный подход: снимок передаётся явно | Изменение проекта во время генерации не меняет basis preview; apply отклоняет устаревший результат |
| Проверки и defaults размножались | DRY: общая оценка кандидата, model defaults/config, общий manual apply | Одиночная проверка и окончательное добавление, quantity/spacing и инвалидация индекса |
| Новые классы могли стать универсальным контекстом | KISS/YAGNI: узкие существующие порты и функции, без service locator в домене | Ни один сценарий не получает весь ProjectApplication |
| Вынос DTO мог изменить сетевой контракт | LSP: совместимые экспорты и неизменные модели | Побайтное равенство OpenAPI и тесты существующих JSON/HTTP потребителей |

Первичные материалы: [FastAPI dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/),
[Pydantic mypy plugin](https://docs.pydantic.dev/latest/integrations/mypy/),
[Ruff configuration](https://docs.astral.sh/ruff/configuration/),
[SRP](https://blog.cleancoder.com/uncle-bob/2014/05/08/SingleReponsibilityPrinciple.html).

## Воспроизведение проверки на Windows

В `apps/api`, с установленным dev-окружением:

```powershell
.venv/Scripts/ruff.exe check
.venv/Scripts/ruff.exe format --check
.venv/Scripts/mypy.exe
$verificationTemp = Join-Path (Get-Location) 'data/windows-dev/backend-final-temp'
New-Item -ItemType Directory -Force -Path $verificationTemp | Out-Null
$env:TEMP = $verificationTemp
$env:TMP = $verificationTemp
.venv/Scripts/python.exe -m pytest --junitxml=data/windows-dev/architecture-backend-final-d.xml
.venv/Scripts/python.exe scripts/export_openapi.py --output data/windows-dev/architecture-openapi.json
Get-FileHash openapi.json,data/windows-dev/architecture-openapi.json -Algorithm SHA256
```

Финальный полный прогон после извлечения сервисов, распределения DTO, удаления
повторного manual apply и расширения архитектурных проверок: **1272 passed**,
214,66 с. Результат: `data/windows-dev/architecture-backend-final-d.xml`.
Предыдущий полный прогон дал 1265 passed и 7 ошибок SQLite из-за заполненного
системного диска C:. Повтор с TEMP/TMP внутри игнорируемого каталога проекта на
D: прошёл целиком; чужие временные файлы не удалялись. Ruff/format и mypy проходят; mypy охватывает 59
перенесённых модулей, а не весь исторический backend. Точный список находится в
`pyproject.toml` и расширяется вместе с миграцией владельцев. Проверка Python
исполнена на локальном 3.14.3; Ruff сохраняет синтаксическую границу 3.11.

OpenAPI SHA-256:
`e2a161f334b8efb8aca08783fa989ae4a26f6ee963019809f8660eba35de4373`.
Рабочая `data/green-atlas.sqlite3` осталась побайтно прежней:
`12154a820adc9d15ef24a8ea5dc4c6d13111b91adfc31bdedd12b6af71fa7c23`.
Тесты используют временные базы; оплачиваемых вызовов модели не было.

Дальнейшее расширение строгой типизации на старые agent workflows и адаптеры —
отдельная миграция их владельцев. Этот срез сохраняет их поведение, не объявляя
полный исторический backend уже приведённым к строгой типизации.
