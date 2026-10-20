# Windows: сборка, установка и границы проверки

Полный запечатанный FULL119 source kit является основанием сборки. Общая ветка
с отдельными платформенными изменениями не заменяет этот комплект: в ней могут
отсутствовать производственный Web, рендер и 14 raw ресурсов frozen API.
Версии независимы: приложение/API/Web 0.1.119, GUI ARX 0.1.48, Core CRX 0.1.49.
Совпадение версий, SHA и source seal не подтверждает выполнение в AutoCAD.

## Что получает пользователь

Один `GreenAtlasSetup.exe` содержит собственный `GreenAtlas.bundle`, frozen API,
скомпилированный Web, desktop host, ARX, CRX, выбранные app-local VC runtime DLL
и проверенный offline Evergreen WebView2 installer Microsoft. Setup работает
для обычного текущего пользователя, не требует Python, Node.js, PowerShell,
MSVC или SDK на его ПК. Установщик с повышенными правами отклоняется.

AutoCAD 2027 x64 остаётся внешним установленным приложением. Blender нужен для
соответствующих операций рендера, его обнаружение не подтверждает результат
рендера. Дополнительные Botaniq модели остаются пользовательским ресурсом и
не являются условием установки или основной работы. Кнопка/инициализация
новой библиотеки этим изменением не реализованы.

Setup размещает bundle в `%APPDATA%\Autodesk\ApplicationPlugins`, создаёт
ярлыки в меню Пуск и собственную запись удаления HKCU. Профиль расположен
в `%LOCALAPPDATA%\GreenAtlas`: `Workspace`, `Transfers`, `WebView2`, `Logs`.
Обновление, откат и удаление bundle эти каталоги не читают и не очищают.

Перед переключением bundle проверяются закрытые AutoCAD/Core/Green Atlas
процессы текущего SID; неизвестный владелец подходящего процесса блокирует
операцию. Проверка повторяется непосредственно перед переименованием staging.
Это не доказывает отсутствие гонки с новым запуском между проверкой и rename;
поведение реальных Windows locks и параллельных сеансов проверяется на Windows.

Повреждённый bundle или неизвестный файл внутри установленного bundle блокирует
замену и удаление. После обновления сохраняется проверенная предыдущая версия;
ярлык «Green Atlas, предыдущая версия» запускает откат. Удаление отключает bundle
через переименование в собственный `.uninstalled-*`, сохраняя программные байты
для восстановления. Setup предлагает удалить только проверенные программные
копии; отдельно доступен `--purge-app-backups`. Проверенные старые Setup EXE
тоже очищаются, кроме текущего запущенного EXE. Его копия с полным встроенным
payload остаётся в `Installer/Versions` для обслуживания и занимает место.

## Подготовка компьютера разработчика

Нужны Windows x64, PowerShell 7, AutoCAD 2027 x64, ObjectARX SDK 2027,
MSVC x64 не ниже 14.44.35207, Windows SDK, CPython из `.python-version`
(сейчас 3.13.15), окружение API по `uv.lock` с native accelerators и инструменты
из `desktop/build-requirements.txt`. `apps/api/scripts/check_runtime.py`
обязателен для frozen API и Setup. Нужны заранее установленные Node.js x64,
`pnpm@11.9.0` и зависимости по lockfile. Сборка отключает сетевую загрузку
Corepack, сама инструменты не устанавливает.

Подготовьте локальные WebView2 SDK 1.0.4191.47 и x64 VC redist directory,
а также Microsoft offline Evergreen installer x64 с известным SHA-256.
Упаковщик проверяет SHA и действительную Microsoft Authenticode подпись
offline installer и скопированных CRT. Evergreen minimum для выбранного SDK:
152.0.4191.53. Старый runtime обновляется включённым offline installer через
неповышенный per-user путь Microsoft. Загрузка bootstrapper не выполняется.

В checkout уже должен лежать полный source seal, соответствующий байтам всех
исходников. Пути ниже задаёт разработчик для своего компьютера; это не настройки
конечного пользователя. Перед каждой командой разработки в той же сессии:

```powershell
. ./scripts/windows-env.ps1
$params = @{
  SourceManifest = 'C:\work\source-kit\source-manifest.json'
  SdkRoot = 'C:\SDK\ObjectARX2027'
  WebView2SdkRoot = 'C:\SDK\Microsoft.Web.WebView2.1.0.4191.47'
  VcRedistDirectory = 'C:\SDK\VC\Redist\x64\Microsoft.VC143.CRT'
  AutoCADRoot = 'C:\Program Files\Autodesk\AutoCAD 2027'
  WebView2Installer = 'C:\SDK\MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
  WebView2InstallerSha256 = '<SHA-256 локального Microsoft offline installer>'
  BuildPython = (Resolve-Path './apps/api/.venv/Scripts/python.exe').Path
}

. ./scripts/windows-env.ps1
./tools/autocad-bridge/build-windows-product.ps1 @params -ValidateOnly

. ./scripts/windows-env.ps1
$setup = ./tools/autocad-bridge/build-windows-product.ps1 @params
```

`ValidateOnly` проверяет локальные предпосылки и инвентаризацию, без MSVC build,
установки или Windows приёмки. Обычный запуск создаёт новые каталоги компонентов
в checkout `.runtime`, собирает GUI/Core/host/API, принудительно задаёт
`VITE_API_URL=''` для свежего Web build и создаёт bundle/Setup. Success stream
содержит путь конечного Setup, диагностика идёт отдельно. Не заменяйте runtime,
host, ARX/CRX или Web после создания manifest.

Опциональный `-SigningCertificateThumbprint` использует существующий сертификат
текущего пользователя для собственных ARX/CRX/host/runtime/Setup до вычисления
окончательных SHA. Он не устанавливает доверие и не обращается к timestamp
серверу. Без параметра результат остаётся неподписанным кандидатом; Windows
репутация/SmartScreen и доверие подписи проверяются отдельно при выпуске.

## Доказательства сборки

`distribution-manifest.json` перечисляет все собственные payload файлы с
относительными путями, размером и SHA, связывает source seal, компоненты и
14 raw ресурсов. Проверка HTML подтверждает только локальные document resources
и отсутствие известных developer origins. Равенство скомпилированного Web
исходникам и API URL не следует из поиска литералов; основание здесь отдельный
свежий build с пустым `VITE_API_URL` и его receipt.

Отдельный `frozen-api-provenance.json` сравнивает код всех sealed `app` модулей
в PYZ, допустимые namespace parents и CArchive `entry.py` с исходниками,
скомпилированными тем же Python. Исключён только `co_filename`, остальные
константы, вложенный код и таблицы строк проверяются. Raw preflight не является
заменой этого доказательства. SDK/внешние библиотеки и PyInstaller hooks имеют
свою сборочную идентичность; это не утверждение о побитовой воспроизводимости.

`dependency-inventory.json` читает normal/delay PE imports, различает payload,
Windows и AutoCAD providers. Worker DLL остаются в Worker, потому что Core job
копирует именно этот каталог. Host и API не могут использовать AutoCAD DLL как
свою зависимость. Каталоги `numpy.libs`, `shapely.libs`, `pyproj.libs` учитываются
адресно как кандидаты wheel hooks, без обещания загрузки. Реальный loader search
в AutoCAD/accoreconsole и frozen API проверяется на Windows; наличие DLL рядом
с host не доказывает доступность зависимости для ARX или CRX.

## Что остаётся выполнить на Windows

Mac проверки включают Python contract/file-policy tests, компиляцию portable C++
политик, проверки синтаксиса и настоящий PowerShell AST parser. Они не выполняют
MSVC, COM shell link, WebView2 window, Job Object Win32 API, AutoCAD ARX/CRX,
offline Microsoft installer или конечный Setup.

После сборки выполните `qualify-windows.ps1` для указанного bundle: он проверяет
manifest/DLL inventory и фиксированные host diagnostics. Отдельный явный
`-RunFrozenSmoke` запускает перемещённый frozen API в checkout с чистыми PATH и
профилем, без Python/Node/SDK из окружения. Команда пишет выполненные проверки,
не превращает candidate автоматически в квалифицированный Windows продукт.

На отдельном обычном профиле проверяются установка, обновление, откат, удаление,
сохранность существующего Workspace, пути с кириллицей/пробелами, runtime без
заранее установленного WebView2/VC, запуск из AutoCAD и меню Пуск, второй запуск,
отмена/закрытие и освобождение дочерних процессов. Нужна сквозная проверка
захвата `/5`, подготовленных faces `/1`, `/2`, `/3`, curve/Region/Hatch, расчёта,
сохранения, повторного открытия и выпуска на AutoCAD 2027. Рендер с установленным
Blender проверяется отдельно. Критерий порта: то же наблюдаемое поведение и
соблюдение контрактов, без требования побитово одинаковых native файлов.

Ориентир 2–4 дня относится к работе коллеги на доступном Windows/AutoCAD 2027
при готовых SDK и зависимостях. Это условный план проверки и исправления
выявленных Windows проблем, не обещанный срок готовой поставки.

Основания: [SDK AutoCAD 2027](https://blog.autodesk.io/autocad-2027-sdk-what-every-plugin-developer-needs-to-know/),
[распространение WebView2](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution),
[WebView2 SDK 1.0.4191.47](https://learn.microsoft.com/en-us/microsoft-edge/webview2/release-notes/sdk/1-0-4191-47),
[app-local VC deployment](https://learn.microsoft.com/en-us/cpp/windows/choosing-a-deployment-method?view=msvc-170).
