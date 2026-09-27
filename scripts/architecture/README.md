# Локальная навигация и защита CAD-архитектуры

Действующий [контракт](../../docs/architecture/autocad-contract.md) и
реестр разрывов (локальный отчёт).
Инструменты не открывают CAD, не меняют проекты и не отправляют код наружу.

## 1. Граф импортов и native include

Из корня репозитория (Windows: сначала `scripts/windows-env.ps1`):

```sh
python3 scripts/architecture/cad_dependencies.py --check
python3 scripts/architecture/cad_dependencies.py --output artifacts/cad-graph-new-run
dot -Tsvg artifacts/cad-graph-new-run/api.dot -o artifacts/cad-graph-new-run/api.svg
dot -Tsvg artifacts/cad-graph-new-run/native.dot -o artifacts/cad-graph-new-run/native.svg
```

`--output` требует НОВЫЙ каталог: старые receipts не перезаписываются.
Python 3.10+ использует стандартный AST, не импортирует само приложение.
Graphviz (`dot`) на проверенном Mac уже установлен; для JSON/DOT он не нужен.
`dependencies.json` содержит каждый статический edge с файлом и строкой;
API DOT сгруппирован по подсистемам, native DOT показывает локальные includes.

Граф **не** является трассой выполнения и не доказывает вызов parser-а.
Он не описывает автоматически связи HTTP/ticket/JSON, инъекцию портов,
TypeScript runtime, виртуальные C++ вызовы и вычисляемые dynamic imports.
Эти межпроцессные связи проверены вручную в схеме аудита. Условия ветвления
всегда читать в коде. Общий граф нельзя использовать вместо проверки кнопки.

## 2. Запрет новых связей с legacy

`legacy-cad-edges.json` фиксирует уже существующие импорты ezdxf, legacy
reader/conversion/AOI entry и вызовы reader/старого import API.
Проверка допускает удаление рёбер, но отказывает при добавлении нового.
Новые вызовы legacy import в другой функции того же файла тоже обнаруживаются.
Модули `cad_bridge`, `geometry`, `planning` не могут добавить прямую такую
зависимость даже через baseline. Относительные/member/literal dynamic imports
проверяются, вычисляемые динамические импорты — нет.

**Это защита от части ошибок, не гарантия нулевого риска.** Она не выявляет
развитие legacy-логики внутри уже разрешённой функции без новых зависимостей
и не сертифицирует транзитивную чистоту. Для этого нужны review, ограничения
ответственности и сквозные тесты. Существующие 158 записей baseline на срезе
аудита — долг; это не 158 запущенных парсеров и не количество дефектов.

Нельзя расширять baseline, чтобы скрыть ошибку. Новое исключение требует
явного архитектурного решения, причины и проверки. Команда автоматического
обновления baseline намеренно отсутствует.

```sh
.venv/bin/python -m pytest scripts/architecture -q
cd apps/api
.venv/bin/python -m pytest -o addopts='' tests/test_cad_architecture.py -q
```

Последний тест включён в обычный backend pytest и существующий headless-профиль.
Отдельного нового CI-сервиса не создано. На другой машине использовать Python
с установленным pytest; сами scanner/check не требуют сторонних Python пакетов.

`apps/api/tests/test_cad_producer_contract.py` отдельно воспроизводит известный
C03 на версиях из текущего native кода. Он помечен strict xfail: это открытый
дефект, а не зелёная приёмка передачи. При исправлении убрать xfail после
положительного контроля; посторонняя ошибка проверки не должна скрываться.

## 3. clangd для native C++/Objective-C++

На текущем Mac `clangd` уже поставляется с Xcode. Подготовлена конфигурация
`tools/autocad-bridge/native/.clangd`, которая указывает на локальную
compilation database. Начальная генерация:

```sh
python3 scripts/architecture/native_compilation_db.py --output .local/code-intelligence/native
clangd --check=tools/autocad-bridge/native/green_atlas_bridge.cpp --compile-commands-dir=.local/code-intelligence/native --tweaks= --log=error
```

Генератор читает compiler options и source list из `build-macos.sh`, сохраняет
SDK/prefix для C++, отдельно flags AppKit для `.mm`; не выполняет сборку.
На Mac с иными путями учитывает `OBJECTARX_SDK_ROOT` и `AUTOCAD_2027_ROOT`.
Индексируется архитектура текущей машины. Self-test macro не включён; это
не замена universal build. После изменения build/SDK создать новый каталог
и обновить путь в `.clangd` (или явно передать `--compile-commands-dir`).
Проверка генератора сверяет весь список native translation units и разделение
AppKit/ObjectARX; новые нестандартные compiler flags требуют review генератора.

Проверены `green_atlas_bridge.cpp`, `hatch_extraction.cpp`, `delivery_ui.mm`.
`--tweaks=` отключает встроенные пробы refactoring actions: Apple clangd при
полном `--check` иначе сообщил ошибку ExtractFunction для break/continue.
Parsing/indexing с SDK после отключения этих проб завершился без ошибок.
Это проверка настройки, а не подтверждение геометрии. LSP-сервер постоянно
в фоне не запущен и UI редактора не перенастраивался.

clangd поддерживает межфайловые references и определения; для полного индекса
ему нужна корректная compilation database. См. [официальные возможности](https://clangd.llvm.org/features)
и [индексацию](https://clangd.llvm.org/design/indexing).

## 4. Что имеет смысл дальше, но не установлено

- [dependency-cruiser](https://github.com/sverweij/dependency-cruiser/blob/main/doc/rules-tutorial.md)
  для графа TypeScript и правил запрещённых связей. Frontend уже имеет ESLint
  layer restrictions; новый инструмент должен дополнить их, не создать второй
  противоречащий набор правил.
- [Import Linter](https://import-linter.readthedocs.io/en/v2.4/contract_types.html)
  для более полного графа Python и транзитивных контрактов. При подключении
  переносить существующие правила, а не делать два независимых baseline.
- MCP над symbol index полезен как интерфейс доступа, но сам не обнаружит
  несовместимость ticket/snapshot или выпуск без исходника. В каталоге
  интеграций подходящий code-graph инструмент по текущему поиску не найден;
  случайные плагины не устанавливались, проект во внешний индекс не передавался.

Вначале достаточно проверенного локального набора и обязательной приёмки
по одному пользовательскому маршруту. Автоматизировать нужно правила, а не
только рисовать красивый граф.
