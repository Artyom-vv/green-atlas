# Проверка собственного desktop payload

`package_preflight.py` создаёт manifest сборки и проверяет поставленный каталог без
запуска Green Atlas, AutoCAD или Blender и без обращения к рабочему профилю.
Это сборочный CLI. Конечному пользователю не нужны Python, Node и source checkout;
тот же проверяющий модуль включается в frozen API через существующий
`collect_submodules("app")`.

```sh
python scripts/desktop/package_preflight.py build \
  --package /absolute/path/GreenAtlasBridge.bundle \
  --source-root /absolute/path/sealed-source \
  --source-manifest /absolute/path/source-manifest.json \
  --output /absolute/path/distribution-manifest.json
python scripts/desktop/package_preflight.py check \
  --package /absolute/path/GreenAtlasBridge.bundle \
  --manifest /absolute/path/distribution-manifest.json
```

В Windows перед каждой командой разработки из корня в той же PowerShell-сессии
выполнить `. ./scripts/windows-env.ps1`. Builder там получает `--layout` с
`PackageLayout`: пять компонентов `desktop`, `api`, `web`, `gui_producer`,
`core_worker`, их относительные `root`/`entrypoint`/`metadata`, независимые версии,
платформу `windows` и архитектуру `x64`. Windows metadata работника читается через
существующий descriptor `resolve_worker_layout`; это статический допуск файлов,
а не квалификация CRX или Core-процесса.

Builder сверяет все перечисленные файлы source seal и 14 необходимых JSON/raw
helpers внутри frozen runtime, затем SHA/размер каждого собственного файла.
Для symlink SHA/размер относятся к записанной относительной цели, а содержимое
реального файла входит отдельной записью. Допустимы только ссылки внутри runtime,
включая внутренние framework directory links. Выход наружу, абсолютные/повреждённые
ссылки, reparse points вне этого контракта, рабочие базы/CAD и developer каталоги
отклоняются. Built Web обязан содержать собственный JavaScript и локальные ресурсы
`index.html`; известный developer API origin в JS отклоняется.

Отчёт фиксирует только `web_document_resources_local` и
`known_developer_origins_absent`. Эти проверки не доказывают, что произвольный
API URL внутри собранного JavaScript равен текущему origin. Production Web
обязательно собирается с `VITE_API_URL=''`; фактическое поведение compiled Web
проверяется отдельно на том же приватном origin.

Проверка source seal сверяет исходные файлы, а проверка 14 raw helpers/JSON — их
байты в runtime. Это не доказывает соответствие Python-кода внутри PYZ этому
source seal. Для квалификации требуется отдельно произведённая квитанция
происхождения compiled API/PYZ и реальных сценариев. CLI не создаёт такую
квитанцию из статического успеха.

Версии desktop, API/Web assembly, GUI producer и Core worker записываются отдельно.
API/Web assembly-версия не извлекается из машинного кода. Producer/worker должны
образовывать явно разрешённую существующим CAD-контрактом пару. Проверка
дополнительно проверяет curated identity Core worker и объявленные протоколы
известной сборки. Для worker49 требуются `/1`, `/2`, `/3` и native-границы
`curve`, `AcDbRegion`, `AcDbHatch`, как в текущем сборщике и archive client.
Эти metadata-capabilities ещё не подтверждают загрузку работника в AutoCAD.
JSON manifest фиксирует состав; доверенная подпись и проверка реальных сценариев имеют отдельные
границы и этим CLI не подменяются.

По умолчанию manifest имеет `status=draft`, а отчёт — `qualification=not_claimed`.
`--qualification` принимает отдельную квитанцию фактически выполненной приёмки:
платформа, точный source seal SHA, payload SHA и все пять component inventories
должны совпасть. Из статического успешного preflight квитанция не создаётся.

`check --discover-external` дополнительно обнаруживает существующие AutoCAD/Blender
через общие read-only descriptors. Статус `installed_not_probed` не означает
готовность CAD/рендера. `user_supplied` у Botaniq описывает отдельно предоставляемые
пользовательские модели; отсутствие этих моделей не делает собственный core payload
повреждённым и не требует изменения пользовательских settings.

Mac `package-macos.sh` сохраняет прежнюю локальную сборку без seal. При явно заданном
`GREEN_ATLAS_SOURCE_MANIFEST` он проверяет seal и final payload после копирования
и переподписи GUI, вкладывает draft `distribution-manifest.json`, затем подписывает
внешний installer. Manifest относится к окончательным байтам вложенного bridge
payload. `GREEN_ATLAS_SOURCE_ROOT` задаёт
проверенный source root, по умолчанию текущий checkout. Ошибка проверки прерывает
упаковку; готовый старый manifest не перезаписывается CLI.

## Настоящий frozen API в Windows CI

`ci-windows-runtime.ps1 -BuildFrozenApi` дополнительно вызывает production builder
`build-desktop-runtime-windows.ps1`. Без switch сохраняется прежняя проверка Win32
и синтетического Setup. Новый source seal включает только обычные tracked файлы
закреплённого Git commit; SHA каждого Git blob сверяется с байтами checkout.
SDK, output, рабочие базы и пользовательские CAD-файлы в него не входят.
Версионированные CAD fixtures хешируются как исходные файлы и не открываются в CAD.

Builder проверяет исходники, весь compiled app/PYZ и bootstrap. Затем 14 raw-ресурсов,
перенесённый runtime с очищенным Python PATH, приватный API, health, завершение
и отказ неизвестному worker проверяются существующими frozen-тестами. Подготовленная
рабочая база, native ticket, пользовательские модели, AutoCAD и Blender не нужны.
Профиль и временные файлы принадлежат checkout, ожидания smoke ограничены.

Workflow сохраняет source seal, Git/provenance/build/CI квитанции, журналы и JUnit.
EXE, весь frozen payload и временные профили не загружаются в artifacts. Квитанции
содержат `product_qualified=false`: эта проверка не является проверкой установки,
собранного Web, WebView2, CAD или рендера.

## Полный Windows Setup

`windows-product-build.yml` собирает весь продукт на Windows x64 из закреплённого
Git checkout: Core CRX, GUI ARX, WebView2 host, frozen API и настоящий compiled Web.
`ci-windows-product.ps1` получает официальные SDK и полный офлайн WebView2 Runtime
по URL/SHA из `windows-product-inputs.json`, проверяет Microsoft Authenticode,
использует app-local CRT из установленного MSVC redist и вызывает production builder.
В установщик не входят SDK, Autodesk DLL/DBX, пользовательские DWG/базы и Botaniq.

Для внешних AutoCAD импортов есть два явных режима. `-AutoCADRoot` проверяет файлы
установленного AutoCAD, включая DBX. Без него `-SdkPackageSha256` в product builder
включает SDK import-контракт. Нижний packager получает `-ObjectArxSdkRoot` и
`-ObjectArxArchiveSha256`: архивный SHA является утверждением вызывающего,
а SHA/размер девяти import libraries и их AMD64 COFF проверяются по закреплённым
значениям. Этот режим допускает AutoCAD providers только у собственных GUI/Core
бинарников. Неизвестные импорты отвергаются, Autodesk runtime не копируется.
Он не подтверждает наличие или запуск установленного AutoCAD.

После сборки отдельный CI harness создаёт временного обычного пользователя
на одноразовом runner. Оригинальный production Setup выполняет установку,
повторное обновление, rollback той же версии, удаление и очистку своих backups.
Проверяются его SID/HKCU, меню Пуск, сохранение пользовательских sentinel-файлов
и созданной API базы. Установленный API работает с полным установленным Web и
чистым системным PATH; HTTP index и ресурсы сверяются с установленными байтами.
Production запрет повышенного token не отключается. Rollback другой версии,
настоящий WebView2 UI, AutoCAD и Blender остаются отдельной приёмкой.

Artifacts содержат конечный Setup, инструкцию, пример двух resource paths и
адресные квитанции/журналы. Профили, рабочие базы, SDK и промежуточные бинарники
не выгружаются. Кандидат остаётся `product_qualified=false` до сквозной приёмки.
