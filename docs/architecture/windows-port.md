# Общий код и границы Windows-порта

Windows использует тот же Web, HTTP API, доменные модели, локальный
проверяющий модуль, ревизии и квитанции сохранения. Карту и Three-просмотрщик
не следует переписывать под native UI. Подготовка, расчёт и выпуск сохраняют
[CAD-контракт](autocad-contract.md).

Совпадение функций проверяется полным пользовательским путём: библиотека,
захват открытого чертежа, назначения слоёв, подготовка, ручная/автоматическая
посадка, сохранение, потерянный ответ, холодное открытие и отдельный CAD-выпуск.
3D, геопривязка и рендер входят в ту же приёмку, если включены в эталонную
Mac-сборку. Производительность проверяется на заявленной Windows-конфигурации.

## Где проходит платформенная граница

| Подсистема | Общая часть | Платформенная ответственность | Проверка |
|---|---|---|---|
| API, Web, посадки | Сценарии, DTO, расчёт, CAS, история, квитанции | CPython/wheels, WebView engine | Одинаковые основания, решения, отказы и ревизии; единые тесты |
| Desktop | Протокол документа, origin, LeaveCoordinator, project work guard | WKWebView/WebView2, активация, cookie, restart, save dialog | Handoff поверх черновика, writing/unknown, смена поколения, PNG/ZIP download |
| Захват | Общие ObjectARX traversal, inventory, семантика, индекс архивных экземпляров | ARX, Win32 delivery/files/XREF и сборка для каждого SDK | Один сохранённый/несохранённый источник, authored/effective layers, ATTRIB, пропущенные XREF |
| Подготовка и выпуск | Протокол faces, topology shapes, пакеты и lifecycle receipts | Поиск AutoCAD/worker, CRX/metadata, Job Objects и pipe supervision | Новые возможности workers, SHA, cleanup, отмена, leader exit с потомком |
| SQLite/профиль | Схема, CAS, согласованный backup/restore | LOCALAPPDATA, byte lock, ACL/reparse/sharing, атомарная публикация | Старый большой профиль, конфликт владельцев, кириллица, rollback без потери данных |
| 3D/рендер | Scene packet, геометрия, Three, Blender scripts, принятие результата | Blender EXE, DLL-поиск, TEMP/TMP, процессное дерево, локальные ресурсы | Свежий и кешированный просмотр, отмена, PNG/receipt, те же модели/текстуры/камеры |
| Геопривязка/окружение | LocationApplication, подтверждение привязки, сохранённый OSM snapshot | Локальная настройка и пользовательские сетевые условия | Привязка и конфликты источника, cold reopen, отказ сети без выдуманных данных |
| Поставка | Manifest, идентичность сборки, приватный API | Native сборка, PyInstaller/wheels, per-user installer и shortcut | Чистая машина без Python/Node, update/rollback/uninstall, целостность ресурсов |

Платформа не выбирает второй алгоритм, не снижает точность и не скрывает
потерянные объекты. Контуры отображения и Blender-сцена не подменяют
подготовленный расчётный снимок.

## Контракты, которые нужно закреплять уже на Mac

1. Версии плагина, native-session, faces и topology shapes принадлежат
   явным метаданным worker. Наличие имени EXE или строки версии не
   подтверждает возможности бинарника. Новая Windows-сборка должна
   согласовать эти поля с текущими Core-запросами.
2. Код подготовки/выпуска зависит от конфигурации установленного AutoCAD,
   worker и задания. Plist и .app остаются в Mac adapter; Windows использует
   собственные metadata/CRX. Эти форматы не дублируют сценарий подготовки.
3. Внешний процесс получает явный executable, рабочую папку и неизменяемый
   вход. Приватные профили, прогресс, отмена и таймаут принадлежат заданию.
   Все результаты проходят прежние SHA/revision/lifecycle проверки.
4. Все frozen-запуски Windows подключаются к одной границе DLL-поиска.
   Она охватывает только создание процесса; чтение stdout, ожидание и
   cancellation не удерживают глобальный lock.
5. Долгие процессы требуют владения до первой инструкции. Для Windows
   это suspended launch и Job Object без breakaway, для Mac — действующий
   POSIX/NSTask adapter. Взаимное завершение посторонних процессов запрещено.
6. Разрешение native-перехода принадлежит общему Web-сценарию. Host не
   выполняет прямой Navigate/Close поверх черновика или неизвестной записи.
7. Ресурсы Blender и просмотра имеют собственные версии/хеши и пользовательские
   пути. Они не зависят от checkout или Mac .runtime и не попадают в Git
   вместе с рабочими проектами.
8. Приёмочные сценарии общие для обеих ОС. Platform adapter тестируется
   отдельно на файлах, процессах и IPC, а не копирует тесты бизнес-логики.

## Реализованная граница DLL-поиска

`app.shared.child_dll_search` переносит установленный механизм Windows
к текущему графу модулей. Для собственного frozen worker DLL-путь остаётся
пакетным. Для внешнего процесса временно используется системный поиск,
затем восстанавливается фактический прежний путь. Подключённые запуски
сериализуются. Mac и обычный Python проходят этот контекст без изменений.

Граница используется владельцами Core-процесса и Python workers импорта.
В изолированном снимке текущего Mac 0.1.119 к ней также подключены Blender,
подготовка preview-assets, явно выбранный Codex provider и проверка версии
прежнего конвертера. Render-изменения передаются отдельным overlay к точному
снимку; они не означают, что старый чистый HEAD содержит весь Mac 0.1.119.
Ожидание и cleanup происходят после восстановления DLL-пути. Аргументы,
лимиты и проверки источника сохранены. Core выбирает отдельные POSIX/Windows
владельцы процессов и файлов; реальные Windows прогоны остаются приёмкой.

Основания: [PyInstaller о внешних процессах](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html),
[Microsoft SetDllDirectoryW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setdlldirectoryw),
[GetDllDirectoryW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getdlldirectoryw)
и [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

## Общая подготовка к установке

`AutoCADInstallation` и `WorkerLayout` отделяют формат установленного ПО от
настройки задания. Mac сохраняет plist, Core и выбор шаблона. Windows descriptor
читает регистрацию AutoCAD2027 и metadata/CRX; наличие этих файлов не
подтверждает выполнение. `worker_config` связывает Windows установку и CRX с
собственным Job Object supervisor и файловым адаптером. Наличие реализации
не заменяет реальный запуск AutoCAD и квалификацию результата.

`native_query.transport.job_files` владеет проверкой source SHA, безопасным
открытием и независимым копированием. Query, подготовка faces, CAD-выпуск
и диагностика используют один публичный вход. Перенесённые алгоритмы
сохранены. Windows использует атомарный JOB_LIST при создании процесса,
ограниченное чтение pipe и отказ при выходе лидера с живыми потомками.
Файловый адаптер удерживает каталоги, проверяет native file ID и reparse points;
наблюдение за временным выходом допускает его атомарную публикацию.

Desktop освобождает handoff, renderer/recognition, SQLite и lease одним
владельцем времени жизни. Ошибка запуска либо одного cleanup не пропускает
остальные действия. NumPy указан прямой зависимостью; существующие версии
lock-файла сохранены. WebView2 message listener передаёт JSON в тот же
origin/generation parser и снимается при закрытии документа.

Полный source overlay содержит `app.distribution` и CLI
`scripts/desktop/package_preflight.py`. Они проверяют относительные пути,
SHA/размеры, независимые component versions, worker capabilities и raw
resources из source seal. Manifest создаётся после окончательной подписи
вложенного payload. Совпадение raw source и файлов не доказывает происхождение
PYZ/Web/native binaries: нужна отдельная квитанция сборки и фактической приёмки.
Web проверка подтверждает локальные ресурсы документа и отсутствие известных
developer origins; desktop Web собирается с `VITE_API_URL=''`.

Ресурсы Botaniq остаются пользовательскими. Кнопка подключения библиотеки,
изменение путей и инициализация каталога отложены по решению пользователя.
Preflight не требует эти модели для установки core и не меняет настройки,
проекты, чертежи или установленное ПО.

Для первой приёмки доступен Windows-компьютер разработчика с AutoCAD2027.
Рабочий ориентир — два или четыре дня при готовых SDK/build environments
и одинаковом ресурсном паке. AutoCAD2026 квалифицируется отдельно.

## Независимые AutoCAD 2026 и 2027

[Autodesk](https://help.autodesk.com/cloudhelp/2027/ENU/AutoCAD-Customization/files/GUID-C21B8F00-C7DE-4E44-8006-D5DC99199F31.htm)
разделяет R25.1/SDK 2026 и R26.0/SDK 2027. Две версии требуют собственных
ARX/CRX, выбора соответствующего worker и независимых GUI/Core прогонов.
Проверка 2027 не подтверждает 2026. Предыдущие Windows-бинарники и старый
ticket `/4` не подтверждают текущий `/5`, native-session и faces.

## Проверка на Windows

Из корня отдельного checkout, перед каждой командой разработки:

```powershell
$apiPython = (Resolve-Path 'apps/api/.venv/Scripts/python.exe').Path

. ./scripts/windows-env.ps1
& $apiPython apps/api/scripts/check_runtime.py

. ./scripts/windows-env.ps1
Push-Location apps/api
try {
    & $apiPython -m pytest tests/test_child_dll_search.py tests/test_child_launch_policy.py tests/test_python_worker.py
} finally {
    Pop-Location
}
```

Используется Python квалифицированного Windows API-окружения по
`apps/api/.python-version` и `uv.lock`. Реальный тест Win32 DLL loader
пропускается на Mac и обязан пройти на Windows.

Успех этих тестов не заменяет packaged Windows-прогон: нужны frozen runtime,
реальные WebView2, AutoCAD2027, Blender, ресурсы и установка на чистой машине.


## Написанный Windows Core-код

`shared.windows_process` владеет деревом, а `windows_process_native` содержит
ленивые Win32 bindings. `PROC_THREAD_ATTRIBUTE_JOB_LIST` связывает процесс с
kill-on-close job в самом CreateProcess, до ResumeThread; HANDLE_LIST передаёт
только собственные stdin/stdout. Это устраняет отдельный промежуток между
созданием suspended процесса и AssignProcessToJobObject. Основание:
[Microsoft UpdateProcThreadAttribute](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute).

`windows_supervision` сохраняет общие бюджеты и cancellation, а
`windows_file_api`/`windows_job_files` обеспечивают SHA на удержанном handle,
независимое копирование, native 128-bit file ID, запрет ADS/неоднозначных имён
и reparse. Файлы результата проверяются после остановки всего дерева и допуска
lifecycle-квитанции. Mac ветки продолжают использовать прежние операции.

`build-windows-2027.ps1` фиксирует текущий source set Core, SDK/toolchain и SHA
результата, создаёт CRX и worker.json с независимой версией49. GUI build также
имеет текущий source closure и четыре Windows адаптера. Compile, реальные
Win32 smoke, кириллица во всём native pipeline и загрузка ARX/CRX проверяются
на Windows.

## Windows GUI, WebView2 и упаковка

`local_platform` владеет закрытыми очередями и файловыми операциями. Windows
GUI adapters сохраняют текущий `/5` захват с archive, inventory, instance
mapping и semantics. Внешние UTF-8 пути переводятся в native filesystem через
`filesystem_utf8.h`; узкие потоки Core query/faces/release также используют эту
границу. Mac ветки сохраняют прежнее представление путей.

Установленный `GreenAtlasDesktop.exe` читает только фиксированную схему
`green-atlas.desktop-launch/1`. Plugin и Start Menu используют один host.
Ticket IPC несёт только 32 hex ID в собственном Transfers. Host владеет API
Job Object до первой инструкции процесса, WebView2 cookie, ready, origin,
поколением документа, native handoff и загрузками. Web продолжает принимать
решение об уходе и защите записи. `/_desktop/shutdown` требует native токен;
после ответа API штатно освобождает профиль перед повторным запуском.

Полный source kit содержит frozen API и package/setup pipeline. Manifest
связывает собственные файлы и независимые component versions; отдельный
`frozen_provenance.py` сверяет код PYZ с sealed Python source, исключая только
`co_filename`. Эти проверки не доказывают выполнение AutoCAD или WebView2.
Установка/обновление переключает только собственный bundle, поддерживает
rollback и сохраняет Workspace/Transfers. Перед заменой bundle текущий
пользователь должен закрыть AutoCAD и Green Atlas во всех своих сессиях.

Windows-ветка включает актуальный продукт из `codex/stable-local-project` вместе
с адаптерами запуска Blender и внешних процессов. Полный Windows build использует
один checkout и manifest его исходников. Прежние sealed FULL119 архивы сохраняют
свои квитанции; они не заменяют новые исходники автоматически.
