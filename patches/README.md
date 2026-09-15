# Патчи сторонних зависимостей

`dxf-viewer@1.0.44.patch` применяется штатным `patchedDependencies` в
`pnpm-workspace.yaml`. Версия SDK закреплена в `apps/web/package.json`,
контрольная сумма патча — в `pnpm-lock.yaml`. Использовать закреплённый в
корневом packageManager pnpm 11.9.0. Ручные изменения production node_modules
не требуются.

Патч исправляет наследование слоя 0, сохранение явного слоя вложенного объекта,
BYLAYER/BYBLOCK и независимые зависимости видимости от родительских INSERT.
`ShowLayers(Record<string, boolean>)` применяет несколько переключений слоёв
за один render; `ShowLayer` делегирует ему. Ключ batch дополнен классом
`line | fill | text`: текстовые треугольники не объединяются с заливками.
`userData.dxfLayer` и `userData.dxfAppearanceClass` публикуют этот контракт
для оформления через публичный `GetScene`. Шейдер получает uniform `opacity`
с исходным значением 1; приложение может клонировать материал и приглушить
только заливки. Геометрические преобразования и native hidden/frozen policy
сохранены. Изменены `BatchingKey.js`, `DxfScene.js`, `DxfViewer.js`,
`TextRenderer.js` и существующее объявление `index.d.ts`.

Upstream: [vagran/dxf-viewer](https://github.com/vagran/dxf-viewer),
Artyom Lebedev и участники проекта. Лицензия MPL-2.0 сохранена в
`dxf-viewer.MPL-2.0.txt`. Исходный npm-пакет:
[dxf-viewer-1.0.44.tgz](https://registry.npmjs.org/dxf-viewer/-/dxf-viewer-1.0.44.tgz).
Изменения перечисленных MPL-файлов распространяются на тех же условиях.
Исходные уведомления об авторстве/лицензии не удалены.

Браузерная сборка содержит `/third-party/dxf-viewer/NOTICE.txt`, лицензию,
исходный npm-архив и точный применённый патч. Эти файлы позволяют получить
полный исходный код этой версии с изменениями независимо от доступности npm.
Копии патча в public и диагностическом стенде должны совпадать с этим файлом.

Проверки выполняются на установленном production SDK:

```powershell
. ./scripts/windows-env.ps1
$env:DXF_VIEWER_SDK_ROOT = (Resolve-Path apps/web/node_modules/dxf-viewer).Path
node --test scripts/cad-sdk-lab/tests/layers.test.mjs scripts/cad-sdk-lab/tests/appearance.test.mjs
```

16 проверок: обе ветки flatten/instancing, вложенные слои, цвета, порядок
hide/show, bulk update с одним render и одним посещением каждого объекта,
координаты, индексированные линии/SOLID и сохранение native filtering.
Четыре новых проверки сравнивают байты буферов, трансформы и SHA примитивов
в мировых координатах с версией предыдущего патча до разделения appearance.
Эталон: `scripts/cad-sdk-lab/tests/appearance-geometry.json`; режимы direct,
flattened, instanced и разбиение длинной polyline на chunks.
У SDK уже был пропуск одного сегмента на границе chunk в синтетической
polyline из 65 540 вершин; этот срез сохраняет прежний результат, не исправляет
этот отдельный дефект и не объявляет полную CAD fidelity.
Счётчики браузерного стенда приведены в
[сравнении SDK](../docs/implementation/2026-09-15-official-implementation/sdk/renderer-comparison.md).
