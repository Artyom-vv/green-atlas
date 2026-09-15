# Проверка готовых CAD SDK на официальном наборе

Отдельный диагностический стенд. Не является новой страницей продукта и не
меняет пользовательские проекты. CAD-файлы запрашиваются только у локального
сервера, оригиналы не копируются и не отправляются сторонним сервисам.

Закреплённые зависимости: dxf-viewer 1.0.44, MLightCAD cad-simple-viewer 1.7.0,
OpenLayers10.9.0, Vite6.4.3; транзитивные версии сохранены в package-lock.json.

На Windows из корня проекта:

```powershell
. ./scripts/windows-env.ps1
# Рабочая копия стенда и все зависимости находятся на D.
npm ci --prefix .runtime/cad-sdk-lab --ignore-scripts --no-audit --no-fund
git apply --check --directory=.runtime/cad-sdk-lab/node_modules/dxf-viewer patches/dxf-viewer@1.0.44.patch
git apply --directory=.runtime/cad-sdk-lab/node_modules/dxf-viewer patches/dxf-viewer@1.0.44.patch
node --test scripts/cad-sdk-lab/tests/layers.test.mjs scripts/cad-sdk-lab/tests/appearance.test.mjs
node .runtime/cad-sdk-lab/openlayers-camera-check.mjs
node .runtime/cad-sdk-lab/node_modules/vite/bin/vite.js .runtime/cad-sdk-lab
```

Исходники этой папки копируются в `.runtime/cad-sdk-lab`; `public/fonts`
подготовлены там из шрифтов того же официального комплекта. Описание и
проверенные хеши: `docs/implementation/2026-09-15-official-implementation/cad/local-fonts.md`.
Не добавляйте сам датасет, шрифты или node_modules в Git. Пути двух разрешённых
локальных CAD-файлов и mtext worker заданы в vite.config.js.

OpenLayers-адаптер и camera-check хранятся здесь вместе с SDK-адаптерами.
Он проверяет существующие Draw/Modify и Escape в отдельном editable overlay;
исходный CAD остаётся отдельным GPU-слоем. Синхронизация камер выполняется
через настоящий OL frameState и локальный origin SDK.

Открыть `http://127.0.0.1:5194`, выбрать SDK и полный файл. Для повторного открытия
перезагрузить страницу: MLightCAD использует singleton. Кнопка участка меняет
только камеру; SDK продолжает хранить полный входной файл. Измерение вызывает
плавное перемещение камеры по синусоиде в течение 10 секунд, затем сохраняет
интервалы requestAnimationFrame, реальные draw calls/буферы и предупреждения
через локальный `/evidence`. Результаты пишутся в `map/sdk-evidence` в документах.

FPS — частота кадров браузера при таком сценарии и данном размере canvas,
не аппаратный GPU timer и не приёмка полноты CAD. Не сравнивать эти числа с
Canvas-рендером приложения как строгий A/B: одинаковый product-сценарий ещё
не измерен. Успешный Load тоже не доказывает поддержку всех CAD entity types.

Неполный первый MLightCAD запуск с неподготовленными шрифтами и остановкой
локального dev server не использовать для сравнительного вывода о скорости.
Установленные библиотеки не изменяются без сохранённого patch и профильных
тестов. dxf-viewer — MPL-2.0; его изменённые исходники и notice должны оставаться
доступны при распространении. В apps/web уже закреплён тот же dxf-viewer с
тем же патчем через pnpm patchedDependencies; продуктовый адаптер принимается
отдельно от этого лабораторного стенда. Исходный SDK, patch иMPL доступны в
public/third-party/dxf-viewer. Готовый архив исходного SDK не содержит датасет.
