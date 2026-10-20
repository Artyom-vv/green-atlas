# API Green Atlas 0.1.133

Источник маршрутов, полей и ошибок — [OpenAPI](../apps/api/openapi.json).
HTTP-обработчики находятся в `apps/api/app`; клиент генерируется в
`packages/api-client`. Этот справочник описывает группы, не дублирует схемы.

## Основные операции

Префикс проектных маршрутов: `/api/projects/{project_id}`.

| Задача | Метод и маршрут после префикса |
|---|---|
| Передача из AutoCAD | `POST /source-autocad-live` |
| Распознавание слоёв | `POST /source-layer-review`, `GET /source-layer-review/status` |
| Назначения | `PUT /layer-mappings` |
| Замечания исходника | `GET /source-read-issues`, `GET /source-object-review` |
| Решение о частичной геометрии | `POST /source-partial-geometry/accept` |
| Подготовка захвата и расчёт | `POST /operations/cad-capture`, `POST /operations/geometry` |
| Состояние, события, отмена | `GET /operations/{operation_id}`, `GET /operations/{operation_id}/events`, `POST /operations/{operation_id}/cancel` |
| Карта и паспорт | `GET /map-features`, `GET /data-passport` |
| Участки | `PUT /planting-zones`, `POST /planting-zones/preview` |
| Проверка места | `POST /plan/placement-check` |
| Предпросмотр расстановки | `POST /plan/automatic/preview`, `POST /plan/patterns/preview`, `POST /plan/brush/preview` |
| Предпросмотр и запись изменений | `POST /plan/change-sets/preview`, `POST /plan/change-sets/apply` |
| Квитанция | `GET /plan/change-sets/{preview_id}/receipt` |
| Рост и сцена | `GET /plan/growth-forecast`, `GET /plan/scene` |
| История | `GET /plan/history`, `POST /plan/history/undo`, `POST /plan/history/redo` |
| Окружение | `GET /location`, `POST /location/alignment`, `POST /location/apply` |
| Подготовка сцены | `POST /scene-render-preparations` |
| Рендер | `POST /scene-renders`, `GET /scene-renders/{job_id}` |
| Изображение / квитанция рендера | `GET /scene-renders/{job_id}/image`, `GET /scene-renders/{job_id}/receipt` |
| Выпуск | `POST /releases`, `GET /releases/{release_id}` |
| Файл выпуска | `GET /releases/{release_id}/artifacts/{artifact_id}` |

Каталог проектов: `GET/POST /api/projects`, `GET/DELETE /api/projects/{project_id}`.
Растения: `GET /api/species` и `GET /api/species/assortment`.
Настройки местоположения: `GET/PUT/DELETE /api/location/settings`.

## Семантика

- API локальный, сеанс принадлежит desktop-приложению. Секрет не включается в ссылки и логи.
- Новый CAD-проект поступает из AutoCAD; HTTP-загрузки DXF/ZIP как второго входа нет.
- Совместимые GET-маршруты сохранённых CAD-ресурсов не являются загрузкой нового проекта.
- Предпросмотр не пишет план. Применение проверяет версии и основание предложения.
- При потерянном ответе сначала читается состояние/квитанция. Повтор записи не выполняется вслепую.
- Долгие операции имеют состояние и отмену; завершение процесса само по себе не доказывает успех.

[CAD-контракт](architecture/autocad-contract.md),
[жизненный цикл данных](<obsidian/02 Архитектура/Жизненный цикл данных.md>).
