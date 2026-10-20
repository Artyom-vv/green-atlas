# Windows desktop host

Win32/WebView2 host показывает общий Web и запускает встроенный API.
Поставка 133 использует те же сценарии и расчётные данные, что общий продукт.

## Состав bundle

От `GreenAtlas.bundle/Contents/Windows`:

```text
GreenAtlasDesktop.exe
green-atlas-launch.json
Runtime/GreenAtlasRuntime.exe
Web/index.html
Worker/GreenAtlasQuery.crx
Worker/worker.json
```

Метаданные `green-atlas.desktop-launch/1` связывают host, runtime, Web и worker.
Обычный запуск без аргументов открывает существующее окно. Плагин передаёт
только `--ticket-id` с ID билета. Произвольные пути, URL, token и команды
из CLI/IPC не принимаются. Named pipe принадлежит текущему SID.

## Владельцы

- `windows-policies.*`: metadata, версии, поколения документа, решения о переходе.
- `windows-platform.*`: Win32, IPC, собственный Job Object, HTTP и файлы загрузок.
- `windows-main.cpp`: окно, WebView2, меню, ready и системные диалоги.

Профиль `%LOCALAPPDATA%\GreenAtlas` содержит Workspace, Transfers, WebView2
и Logs. Проверяются владелец/ACL и reparse points.

## Переходы и ошибки

Только текущий top-document и сеанс могут подтверждать готовность или уход.
Незавершённая запись защищена общим leave guard. Сбой renderer не считается
сохранением; перезапуск API не объявляет повреждённый документ восстановленным.
Новое поколение API получает новый token. Запись и передача не повторяются скрыто.

Остановка сначала просит runtime завершиться, затем очищает только собственное
дерево. Системные загрузки ограничены разрешёнными маршрутами текущего origin;
результат проверяется и публикуется через отдельный временный файл.
Внешний браузер не получает секрет сеанса.

## Сборка и проверка

Сборщик: `tools/autocad-bridge/build-desktop-windows.ps1`.
Нужны MSVC x64, Windows SDK, WebView2 SDK 1.0.4191.47.
Перед каждой Windows-командой: `. ./scripts/windows-env.ps1`.
[Полная сборка](../WINDOWS-PACKAGING.md),
[принятые проверки 133](<../../../docs/obsidian/04 Качество и показатели/Качество и показатели.md>).
