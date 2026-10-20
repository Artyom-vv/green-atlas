# Windows desktop host

`windows-main.cpp` реализует отдельный Win32/WebView2 host текущего интерфейса.
Mac-оболочка и общие `contracts.h/cpp` этим участком не меняются. Квалификация
настоящего Windows EXE, AutoCAD 2027 и чистого установочного профиля ещё требуется.

## Состав и запуск

Все пути ниже отсчитываются от `GreenAtlas.bundle/Contents/Windows`:

```text
GreenAtlasDesktop.exe
green-atlas-launch.json
Runtime/GreenAtlasRuntime.exe
Web/index.html
Worker/GreenAtlasQuery.crx
Worker/worker.json
```

Метаданные содержат ровно этот объект:

```json
{"schema":"green-atlas.desktop-launch/1","kind":"windows_webview2","host":"GreenAtlasDesktop.exe","runtime":"Runtime/GreenAtlasRuntime.exe","web":"Web","worker":"Worker"}
```

Обычный запуск не требует аргументов. Плагин передаёт только
`--ticket-id <32 lowercase hex>`. Пути, URL, token и команды из CLI либо IPC
не принимаются. Повторный процесс передаёт этот ID через приватный named pipe
с ACL текущего SID. Запуск без ID поднимает существующее окно.

Профиль: `%LOCALAPPDATA%\GreenAtlas`. `Workspace`, `Transfers`, `WebView2` и
`Logs` принадлежат текущему пользователю. Существующие owner/ACL проверяются,
чужое владение и широкие права не исправляются скрыто. Reparse points у любого
компонента отклоняются. Билет: `Transfers/<id>/transfer.gatransfer`.

## Владельцы

- `windows-policies.*` проверяют launch metadata, адреса, версии, JSON каналов,
  поколения документа и решения общего `LeaveCoordinator`.
- `windows-platform.*` владеют Win32 handles, приватным IPC, API job, HTTP и
  независимыми временными файлами загрузок. API попадает в Job Object атомарно
  через `JOB_LIST`; наследуются только собственные stdin/stdout/stderr handles.
- `windows-main.cpp` владеет окном, WebView2, cookie, асинхронными заданиями,
  меню, save dialog и наблюдением журнала передач. Draft/write policy остаётся
  у текущего Web, подготовка CAD у API и AutoCAD.

Ready читается из приватного pipe по доступным байтам, с лимитом 16 KiB.
Долгие HTTP-запросы выполняются вне UI thread, JSON-ответ ограничен 32 KiB.
`POST /_desktop/handoffs` передаёт только `ticket`. Неопределённый результат
не вызывает повторный POST. Продолжение сохранённой передачи требует выбора
и подтверждения пользователя через отдельный `/resume`.

Только top-document сообщения текущего origin могут подтвердить ready или
решение о переходе. Устаревшие generation/request ID не принимаются. Закрытие,
переход к библиотеке и открытие другого проекта проходят общий leave guard.
До первой готовности нового документа закрытие разрешено: запись из него
ещё не была разрешена. Загруженный документ сохраняет guard при остановке API.
Падение browser/главного renderer и его зависание переводят документ в отдельное
состояние ошибки. Старые решения о переходе и повторный ready отклоняются;
API restart не объявляет такой документ восстановленным. При закрытии уже
готового документа native-диалог требует явного подтверждения: подтверждение
сохранения недоступно, последние изменения могли не сохраниться. Перезагрузка
документа и повтор записи автоматически не выполняются.

Остановка сначала запрашивает native-only `POST /_desktop/shutdown` с `{}`,
затем ожидает завершения всего собственного job. После 10 секунд применяется
`TerminateJobObject`. Перезапуск занимает прежний порт через `--listen-port`,
требует нового token и заменяет HttpOnly/SameSite=Strict cookie. Готовый Web
документ сохраняется; старая запись и ticket автоматически не повторяются.

Загрузки принимаются только из текущего origin по release-artifact и
scene-render-image маршрутам. Файл выбирается save dialog; PNG/ZIP дополнительно
проверяются по сигнатуре, все загрузки потоковые, ограничены и публикуются
через отдельный временный файл. Квитанции в `Logs` содержат bytes/SHA/тип/маршрут,
без session token. Сохраняются отдельные CSV, JSON, SVG и DXF текущего выпуска.
WebView2 callback только отменяет встроенную загрузку и сохраняет plain данные.
Save dialog и запуск системного браузера отложены до возврата callback, чтобы
не создавать запрещённый WebView2 nested message loop. До и после save dialog
проверяются поколение runtime, origin/token, доступность сервиса и документа.
Совместимость сохранённых DXF сохраняет существующую кнопку source-dxf/download:
принимается точный маршрут, optional ожидаемый SHA256 и только DXF MIME/расширение.
Внешняя HTTP/HTTPS ссылка открывается системным браузером только по действию
пользователя, без переноса приватной сессии. File/custom scheme и credentials
не допускаются.

## Сборка разработчика

Нужны Developer PowerShell x64 MSVC, Windows SDK и локальный NuGet
`Microsoft.Web.WebView2 1.0.4191.47`. Скрипт проверяет nuspec и не скачивает SDK.
Static loader не требует отдельного `WebView2Loader.dll`. Установщик поставляет
VC++ x64 и Evergreen WebView2 Runtime не ниже `152.0.4191.53`.

```powershell
. ./scripts/windows-env.ps1
./tools/autocad-bridge/build-desktop-windows.ps1 -WebView2SdkRoot C:/SDK/WebView2 -OutputDirectory .runtime/host-build -ValidateOnly

. ./scripts/windows-env.ps1
./tools/autocad-bridge/build-desktop-windows.ps1 -WebView2SdkRoot C:/SDK/WebView2 -OutputDirectory .runtime/host-build -Version 0.1.119
```

Сборка выполняет portable policies и отдельный synthetic Win32 runtime smoke:
silent/oversized ready, выход лидера с живым потомком и остановка принадлежащего
дерева. Эти тесты используют только каталог сборки и собственные тестовые EXE.
Они не открывают WebView2, AutoCAD или пользовательскую БД. Build receipt рядом
с EXE связывает SHA бинарника с SHA/bytes исходников и внешних SDK inputs.
Optional `-SigningCertificateThumbprint` выбирает локальный сертификат
`Cert:\CurrentUser\My` с private key. Подпись SHA256 ставится до фиксации SHA
бинарника; скрипт не загружает сертификаты и не меняет доверие системы.

CLI `--self-test`, `--build-info`, `--status-json` возвращает JSON и не создаёт
пользовательский профиль. `--self-test` подтверждает portable policy, а не GUI.
`--status-json` проверяет layout и доступную версию WebView2 без запуска API.

```powershell
. ./scripts/windows-env.ps1
$env:GREEN_ATLAS_DESKTOP_TEST_EXE = 'C:/candidate/GreenAtlas.bundle/Contents/Windows/GreenAtlasDesktop.exe'
python -m pytest tools/autocad-bridge/desktop/test_windows_host.py
```

## Приёмка на Windows

На отдельном чистом тестовом профиле проверить установку без dev runtime,
первое окно и раннее закрытие, ready/leave guard для dirty/unknown write,
падение/зависание renderer с отказом и подтверждением закрытия,
передачу `/5` и отказ повреждённого билета, recovery/cancel/resume,
остановку API и same-port restart с сохранённым документом, PNG и ZIP через
save dialog с совпадением SHA квитанции, отдельные файлы выпуска, внешние ссылки
и повторный GAOPEN во время уже открытого приложения. Удаление приложения
и обновление не должны удалять Workspace/Transfers. До этой приёмки комплект
остаётся кандидатом; запуск portable тестов на Mac её не заменяет.

Основания: [STARTUPINFOEX/JOB_LIST](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute),
[WebView2 top-document messaging](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2),
[static loader](https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/static),
[SDK/runtime compatibility](https://learn.microsoft.com/en-us/microsoft-edge/webview2/release-notes/sdk/1-0-4191-47).
[WebView2 reentrancy](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/threading-model#reentrancy),
[ProcessFailed](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2#add_processfailed).
