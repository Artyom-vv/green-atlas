# Реальный нагрузочный чертёж ВДНХ

`vdnkh.osm.json` — снимок открытых данных OpenStreetMap для территории вокруг ВДНХ в Москве: здания, дороги, зелёные территории, природные объекты и гидрография. Граница выборки: `55.8210, 37.6210, 55.8380, 37.6500`. Время базы OSM: `2026-08-09T19:57:01Z`.

Данные получены через Overpass API и используются только как воспроизводимая нагрузочная фикстура. Авторство: © OpenStreetMap contributors. Лицензия: Open Database License, https://www.openstreetmap.org/copyright.

Повторное создание DXF:

```bash
apps/api/.venv/bin/python fixtures/large-map/generate_vdnkh_dxf.py
```

Слои DXF:

- `SITE_BORDER` — граница загруженной территории;
- `OSM_BUILDING` — реальные контуры зданий;
- `OSM_ROAD_MAJOR` — магистральные и связующие дороги;
- `OSM_ROAD_LOCAL` — местные дороги, проезды и пешеходные пути;
- `OSM_GREEN_EXISTING` — парки, газоны и природные территории;
- `OSM_HYDROGRAPHY` — водные объекты;
- `OSM_REFERENCE` — оставшаяся справочная геометрия.

Файл предназначен для проверки импорта, дерева слоёв, масштаба, навигации, уровней детализации и производительности. Он не является кадастровым или инженерным планом и не должен использоваться для проектных расчётов.

## Пространственная версия

`generate_vdnkh_3d_dxf.py` создаёт отдельный `vdnkh-3d.dxf`, не изменяя
плоскую нагрузочную фикстуру. XY и контуры берутся из зафиксированного
`vdnkh.osm.json`. Рельеф берётся из Copernicus DEM 2021 GLO-90 через
Open-Meteo Elevation API и хранится в воспроизводимом кэше
`vdnkh.copernicus-glo90.json`. Обязательная атрибуция: Copernicus Programme и
Open-Meteo. GLO-90 — это DSM с шагом около 90 м, а не инженерная топосъёмка;
официально заявлены абсолютная вертикальная точность < 4 м (90% LE) и
горизонтальная < 6 м (90% CE). Поэтому DXF явно маркирует поверхность как
`estimated`. Условия и обязательное уведомление зафиксированы в кэше и в
[лицензии Copernicus DEM](https://dataspace.copernicus.eu/sites/default/files/media/files/2025-06/copernicus_contributing_mission_data_access_v2_cop_dem_licenses.pdf).

Z нормализован относительно минимальной абсолютной отметки выборки (134 м
над уровнем моря). Источник, URL, атрибуция, доверие, vertical datum и ASL-offset
записаны в DXF custom properties; одного имени terrain-слоя недостаточно для
его подтверждения импортёром.

Для зданий явный OSM `height` хранится в
`GREEN_ATLAS_BUILDING_OSM_HEIGHT`, а оценка по `building:levels` — отдельно в
`GREEN_ATLAS_BUILDING_OSM_LEVELS` (3 м на этаж плюс `roof:height`). Здания без
таких тегов не получают выдуманную высоту.

```bash
apps/api/.venv/bin/python fixtures/large-map/generate_vdnkh_3d_dxf.py
# Только для сознательного обновления checked-in DEM:
apps/api/.venv/bin/python fixtures/large-map/generate_vdnkh_3d_dxf.py --refresh-dem
```

Эта версия остаётся демонстрационным OSM/DEM-контекстом, а не инженерной
топосъёмкой. GLO-90 имеет ограниченное разрешение, а высоты по этажности
являются оценочными; provenance должен оставаться видимым пользователю.
