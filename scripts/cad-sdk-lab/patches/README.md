# Патч слоёв и appearance dxf-viewer 1.0.44

Патч квалифицирован в `.runtime/cad-sdk-lab` и теперь подключён также в apps/web
через pnpm patchedDependencies. Основной файл — `patches/dxf-viewer@1.0.44.patch`;
эта копия нужна для воспроизводимости стенда. Исходная версия —
`dxf-viewer@1.0.44` из npm; контрольные суммы пяти исходных и изменённых
файлов находятся в соседнем JSON. Суммы нормализуют переводы строк в LF.

В исходном SDK слой INSERT заменяет явный слой вложенной сущности при
flattening и создании экземпляров. Кроме этого, слой `0` и цепочка BYBLOCK
теряют часть контекста вложенных вставок. Патч сохраняет:

- явный ненулевой слой сущности; слой `0` наследует эффективный слой INSERT;
- разные источники цвета: BYLAYER — эффективный слой, BYBLOCK — свойства
  ближайшего INSERT с дальнейшим наследованием при необходимости;
- зависимости видимости от родительских вставок отдельно от собственного
  слоя сущности; включение дочернего слоя не показывает скрытого родителя;
- отдельные batch keys для разных зависимостей видимости, включая вложенные
  вставки-соседи и несколько экземпляров одного блока;
- одного владельца удаления геометрии при нескольких зависимостях видимости.

`ShowLayer` сохраняет контракт viewer: выключенный родительский слой
подавляет зависимые части блока. Это не утверждение о полной идентичности
всех режимов AutoCAD Off/Frozen. Исходные фильтры hidden/frozen, математические
трансформы, buffer layouts и instancing не изменены. Патч не добавляет
отсечение геометрии или выборочное удаление сущностей.

Пять изменённых файлов: `src/BatchingKey.js`, `src/DxfScene.js`,
`src/DxfViewer.js`, `src/TextRenderer.js`, `src/index.d.ts`. `ShowLayers` применяет несколько изменений
видимости за один render, а `ShowLayer` делегирует ему. В существующем d.ts
добавлены этот метод и объявление существующего runtime `DefaultOptions`.
Небольшой дополнительный маркер цвета нужен для случая
«вложенный INSERT на слое 0 с BYLAYER → объект на явном слое C с BYBLOCK»:
цвет должен прийти со слоя внешней вставки, а не со слоя C или из явно
назначенного цвета внешнего INSERT.

Ключ batch теперь отдельно сохраняет `line | fill | text`. HATCH, SOLID и
3DFACE относятся к fill; glyph geometry из TextRenderer — к text, включая
TEXT/MTEXT/ATTRIB и текст DIMENSION. Линии, символы точек и стрелки размеров
остаются line. Класс проходит через flatten, вложенные INSERT, instancing и
chunks. Публичные `userData.dxfLayer` и `userData.dxfAppearanceClass` позволяют
оформлять готовую сцену без разбора исходника ещё раз. Shader получает
`opacity` по умолчанию 1; исходный режим CAD остаётся непрозрачным.
Это классификация оформления, а не новое правило определения смысла сети
или доказательство нормативной пригодности геометрии.

Установщик npm может заменить содержимое `node_modules`. После чистой
установки именно версии 1.0.44 применить из корня репозитория:

```powershell
. ./scripts/windows-env.ps1
git apply --check --directory=.runtime/cad-sdk-lab/node_modules/dxf-viewer scripts/cad-sdk-lab/patches/dxf-viewer-1.0.44-layer-inheritance.patch
git apply --directory=.runtime/cad-sdk-lab/node_modules/dxf-viewer scripts/cad-sdk-lab/patches/dxf-viewer-1.0.44-layer-inheritance.patch
node --test scripts/cad-sdk-lab/tests/layers.test.mjs scripts/cad-sdk-lab/tests/appearance.test.mjs
```

Повторно применять поверх уже изменённых файлов не нужно. После применения
перезапустить тестовый Vite с обновлением dependency cache: worker и основной
модуль должны использовать одну версию внутреннего формата batches.

Проверено: 16 Node-тестов, включая bulk update с единственным render, обе ветки
flatten/instancing, вложенные соседние вставки, порядок hide/show,
BYLAYER/BYBLOCK, индексированные линии,
SOLID и координаты после basepoint/nonuniform scale/rotation. Тесты вызывают
настоящие `DxfScene.Build`, `DxfViewer.Load`, Batch, Layer и ShowLayer;
подменяются только WebGL drawing, DOM events и транспорт worker.
На чистом npm-архиве1.0.44 `git apply --check` и применение проверены;
результат сравнивается по всем пяти контрольным суммам. Лог исходного запуска:
`.runtime/verification/dxf-sdk-layers.txt`.

Дополнительный эталон `../tests/appearance-geometry.json` получен на предыдущем
production layer patch. Direct, flattened, instanced и chunked fixtures после
appearance совпадают по размеру vertex/index/transform buffers, SHA матриц и
SHA примитивов после перевода в мировые координаты. Новый лог:
`.runtime/verification/sdk-appearance-production.txt`.
В длинной polyline из 65 540 вершин выявлен прежний пропуск одного сегмента
на границе chunk. Сравнение сохраняет этот результат; исправление отдельное.

Эти Node-тесты не являются GPU-проверкой. Отдельная браузерная проверка
OpenLayers+SDK после исправления слоёв: полный APOT открылся за5471,2мс,
483 draw calls,169,803FPS; артефакт (локальный отчёт).
Bulk ShowLayers и appearance добавлены позже и проверены профильными тестами.
Эти показатели относятся к версии до дополнительного разделения batches по
appearance: её новые draw calls и кадры требуют отдельного замера. Лабораторные
кадры не являются автоматической приёмкой производственного интерфейса.
Не решены entity picking, редактирование исходных CAD-сущностей, line patterns,
полная поддержка OCS/форматов, динамический LOD и остальные ограничения SDK.
Существующее сочетание group 420 TrueColor с ACI 0/256 в parser остаётся
отдельным исправлением; parser этим патчем не изменён.

Правило наследования основано на
[Autodesk Block Object Properties](https://help.autodesk.com/cloudhelp/2026/ENU/AutoCAD-Core/files/GUID-25E9F20C-D146-426C-8815-37DF48D2D33F.htm)
и проверке реализации `resolve_layer`/`resolve_color`
[ezdxf](https://github.com/mozman/ezdxf/blob/master/src/ezdxf/addons/drawing/properties.py).
Механизм видимости INSERT у ezdxf отличается; он не использовался как
единственный эталон AutoCAD layer visibility.

Upstream — [vagran/dxf-viewer](https://github.com/vagran/dxf-viewer),
лицензия [MPL-2.0](https://github.com/vagran/dxf-viewer/blob/master/LICENSE).
Изменения этих пяти файлов сохраняют MPL-2.0 и исходные уведомления.
При распространении браузерной сборки нужно предоставить соответствующий
исходный код MPL-части с изменениями и уведомить, где его получить; отдельные
новые файлы без заимствованного MPL-кода не становятся автоматически MPL.
[Разъяснение Mozilla](https://www.mozilla.org/en-US/MPL/2.0/FAQ/).
