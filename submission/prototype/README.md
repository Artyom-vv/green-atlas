# Установка Green Atlas

**[Скачать Green Atlas 0.1.133 для Windows x64](https://github.com/Artyom-vv/green-atlas/releases/download/v0.1.133-windows/GreenAtlas-0.1.133-Windows-x64-Setup-2.exe)**

## Поддерживаемая конфигурация

| Компонент | Версия | Назначение |
|---|---|---|
| Windows | 11, x64 | Запуск приложения |
| AutoCAD | 2027, полная версия | Открытие DWG и выпуск чертежа |
| [Blender](https://www.blender.org/releases/4-5/) | 4.5 LTS | Подготовка детальных моделей и рендер |
| Модели и текстуры | Пак Botaniq/PBR из комплекта | Детальное оформление 3D |

[Системные требования AutoCAD 2027](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/System-requirements-for-AutoCAD-2027-including-Specialized-Toolsets.html).

## Установка

1. Установите и активируйте AutoCAD 2027. Для детального 3D и рендера установите Blender 4.5 LTS.
2. Скачайте `atriune_Green-Atlas_Botaniq-PBR.zip` из [папки материалов](https://disk.yandex.ru/d/vEO0vpO6jMGhDQ) и распакуйте целиком.
3. Закройте AutoCAD и Green Atlas. Запустите установщик Green Atlas.
4. В мастере укажите папку AutoCAD, файл `blender.exe` и папки ресурсов:

| Поле мастера | Папка внутри распакованного архива |
|---|---|
| Библиотека Botaniq | `GreenAtlas-RenderResources/RenderResources/botaniq` |
| PBR-текстуры | `GreenAtlas-RenderResources/RenderResources/pbr` |

5. Завершите установку. Пути можно изменить в приложении кнопкой «Компоненты».

Установщик содержит приложение и плагин AutoCAD. Проекты и расчёты хранятся
локально. Для AI, адресного поиска и картографических данных нужен интернет.

## Первый проект

1. Скачайте и распакуйте [чертежи Кустанайской](Kustanayskaya-CAD.zip), сохранив все подпапки.
2. Откройте в AutoCAD файл `05_10004141_Генплан.dwg` из папки `Кустанайская`.
3. Выполните команду `GAOPEN`. Откроется Green Atlas.
4. Проверьте назначения слоёв, выберите границу и запустите подготовку карты.
5. Создайте рабочий участок, выберите растения, проверьте места и примените посадки.
6. Сохраните план. Переключитесь в 3D для просмотра; нажмите «Выпуск» для чертежа и ведомости.

Пропуски исходных данных отображаются в замечаниях проекта.

[Статус выпуска и ограничения](https://github.com/Artyom-vv/green-atlas/releases/tag/v0.1.133-windows),
[контрольная сумма установщика](Windows-0.1.133-SOURCE.json),
[демонстрация](../materials/README.md).
