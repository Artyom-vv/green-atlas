# Нативная сборка Windows для AutoCAD 2027

`build-windows-2027.ps1` готовит кандидаты Core CRX и GUI ARX из текущих исходников.
Он не устанавливает файлы в AutoCAD, не открывает чертежи, не изменяет профиль
пользователя и не подтверждает работоспособность готового продукта на Windows.
По умолчанию выбрана цель `Core`. Цель `Gui` использует общие C++ модули
и четыре Windows адаптера; обе цели требуют реальной Windows приёмки.

## Состав и границы

`build-plan.json` хранит 28 C++ единиц Core и 52 C++ единицы Windows GUI.
Mac GUI использует 48 общих C++ единиц и два Objective-C++ адаптера. Тест
сопоставляет общую часть с действующими Mac builders, проверяет реализации зарегистрированных Core
команд и возможности подготовки областей по текущему `package_faces.cpp`.

Версии компонентов независимы: версия приложения читается из общего
`tools/autocad-bridge/PackageContents.xml`, GUI плагин 0.1.48,
Core работник 0.1.49. Core поддерживает объявленные текущими исходниками
`green-atlas.archive-face-preparation/1`, `/2`, `/3` и формы `curve`,
`AcDbRegion`, `AcDbHatch`. Эти сведения попадают в `worker.json` после успешной
компиляции. Наличие метаданных не доказывает выполнение команд в AutoCAD.

Платформенные ветки файлового адаптера используют SHA-256 через BCrypt,
UTF-8 пути через UTF-16 файловую систему и замена файла через `MoveFileExW`.
Общие имена загружаемых модулей сохраняют Mac значения и выбирают `acbr26.dbx`
на Windows. Публичные интерфейсы расчётных модулей не меняются.

`local_platform` владеет очередями, закрытыми каталогами текущего пользователя,
случайными идентификаторами и файловыми операциями. Windows adapters владеют
прогрессом и отменой подготовки, поиском XREF, передачей `/5` и запуском
фиксированного установленного WebView2 host с `--ticket-id`. Native-session `/3`,
архив, inventory, semantics и SHA остаются в текущем общем контракте. Старый
Windows `/4` не используется вместо актуального захвата.

## Среда разработчика

Нужны PowerShell 7 (`pwsh`), распакованный **Windows ObjectARX SDK 2027**,
MSVC x64 и Windows SDK.
Используются каталоги `inc`, `inc-x64`, `lib-x64` и BRep `utils/brep` именно
этого SDK. Параметры C++20, Unicode, `/MD`, conformance mode и SDK
`AcRxDefault.def` соответствуют [параметрам Autodesk для 2027](https://blog.autodesk.io/autocad-2027-sdk-what-every-plugin-developer-needs-to-know/).
Минимальная проверяемая версия MSVC tools: `14.44.35207`. Сборщик принимает
подготовленный Developer PowerShell x64 либо находит Visual Studio через
`vswhere`; можно задать `-VisualStudioRoot`. Ни SDK, ни компилятор не скачиваются.

Список библиотек сверен с официальным Windows ObjectARX SDK 2027.
`acgex26.lib` находится в `utils/brep/lib-x64` рядом с `acbr26.lib`, а не в
общем `lib-x64`. Она требуется для точного `AcGeExternalCurve3d::isNativeCurve`;
расчётный алгоритм сохраняет этот путь. Список исходников сверен с текущим
Core 49. Старые бинарные файлы не переиспользуются.

Финальная строка CL response file содержит `/link` и все параметры LINK вместе.
Разделение этих параметров переводами строк приводит к разбору `/OUT` и
`/LIBPATH` как параметров компилятора. Общий расчётный файл явно подключает
`<stdexcept>`; Windows prefix предварительно объявляет `CPoint`, используемый
SDK только в объявлениях функций. MFC runtime для этого не подключается.

Каждую команду выполнять из корня checkout, предварительно подключая окружение
в той же PowerShell сессии:

```powershell
. ./scripts/windows-env.ps1
& ./tools/autocad-bridge/build-windows-2027.ps1 -Target Core -SdkRoot 'C:\SDK\ObjectARX2027\CDROM1' -ValidateOnly
```

```powershell
. ./scripts/windows-env.ps1
& ./tools/autocad-bridge/build-windows-2027.ps1 -Target Core -SdkRoot 'C:\SDK\ObjectARX2027\CDROM1'
```

Проверка предпосылок GUI и последующая компиляция:

```powershell
. ./scripts/windows-env.ps1
& ./tools/autocad-bridge/build-windows-2027.ps1 -Target Gui -SdkRoot 'C:\SDK\ObjectARX2027\CDROM1' -ValidateOnly
```

```powershell
. ./scripts/windows-env.ps1
& ./tools/autocad-bridge/build-windows-2027.ps1 -Target Gui -SdkRoot 'C:\SDK\ObjectARX2027\CDROM1'
```

Тесты используют только стандартную библиотеку Python. После подготовки
проектного окружения разработчика:

```powershell
. ./scripts/windows-env.ps1
$apiPython = Join-Path $PWD 'apps/api/.venv/Scripts/python.exe'
& $apiPython -B ./tools/autocad-bridge/windows-2027/test_build_plan.py -v
```

Нативные результаты сборки находятся внутри checkout; каталог по умолчанию:
`.runtime/autocad-bridge/windows-2027/core`. Временные файлы компилятора
создаются в новом `build-<id>`, объекты предыдущей сборки не используются.
PS1 и response файл сохраняют UTF-8 с BOM для Unicode путей компилятора.
Для запуска используется PowerShell 7.

## Результаты проверки и сборки

`source-inventory.json` детерминирован для неизменённого checkout: относительные
пути, размеры, SHA-256, версия компонента и список единиц компиляции. Он включает
используемые C++ файлы, локальные заголовки, план и builders. До проверки SDK
реестр записывается даже при его отсутствии. `validation-receipt.json` отличает
готовность предпосылок от компиляции и всегда содержит
`autocad_execution_qualified: false`. `-ValidateOnly` не вызывает CL.

После успешного CL дополнительно проверяется архитектура PE AMD64 и отсутствие
изменений исходников за время сборки. В отдельном каталоге сборки остаются
копия реестра, `compile.rsp`, `build-receipt.json`, `package/GreenAtlasQuery.crx`
и `package/worker.json`. Квитанция связывает исходники, SDK файлы, версии
инструментов и SHA бинарного файла. Это кандидат для дальнейшей приёмки.

На Mac проверены состав и переносимые контракты, реальные операции `file_io`
и UTF-8 filesystem с кириллицей, SHA и атомарная замена. Полные Mac GUI и Core
собраны и слинкованы с настоящими SDK и AutoCAD 2027 frameworks, без установки
или открытия пользовательских чертежей. Это подтверждает компиляцию Mac веток.
PowerShell parser проверяет AST скриптов, не исполняя Windows команды.

До признания Windows кандидатов пригодными нужны MSVC сборка, загрузка ARX/CRX,
`GAOPEN`, запрос, подготовка `/3` и выпуск, кириллица и длинные пути, ошибки XREF,
тайм-аут, отмена и подтверждённое освобождение базы. Преобразования путей Core
и общих GUI файлов используют `filesystem_utf8.h`; работа UTF-16 ветки и Win32
sharing/ACL проверяется на реальной Windows.

Полная приёмка отдельно проверяет Windows runner и Job Object, WebView2,
frozen API, Blender и чистую установку без dev окружения. Нативный build recipe
сам по себе не является установщиком и не распространяет SDK конечному
пользователю. AutoCAD 2026 этой сборкой не квалифицируется.

## Отдельная MSVC проверка в CI

Workflow `windows-native-build.yml` запускается вручную с обязательными URL
официального Windows ObjectARX 2027 архива и его отдельно проверенным SHA256.
Условия получения SDK согласуются до запуска. Принимается прямой HTTPS URL
домена Autodesk; перенаправления не выполняются. Проверенный ZIP или SFX
распаковывается через 7-Zip без запуска SDK EXE. Инструменты и SDK находятся
только в `.runtime` checkout. AutoCAD на runner для компиляции не нужен.

`ci-windows-native.ps1` включает Developer Shell MSVC x64 и независимо строит
Core и Gui через действующий production builder: ошибка одной цели не скрывает
ошибку другой. `dumpbin` проверяет два обязательных экспорта каждой DLL,
не загружая её в процесс. Полученные ARX/CRX остаются кандидатами.

В CI artifact разрешены только собственные ARX/CRX, их metadata, журналы,
исходные реестры и квитанции. SDK, Autodesk DLL/import libraries, объекты и PDB
не выгружаются. Статус этой проверки означает compile/link; запуск AutoCAD,
захват, подготовка и выпуск не выполняются. Успешный CI не подтверждает полный
Windows продукт или совместимость с Windows Server как пользовательской ОС.
