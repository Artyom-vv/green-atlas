/** Настройки и объяснения локального транспорта, общие для API-фасадов. */
export const LOCAL_READ_TIMEOUT_MS = 60_000;
export const LOCAL_MAPPING_ACK_TIMEOUT_MS = 30_000;
export const MAPPING_ACK_TIMEOUT_MESSAGE =
  'Сохранение назначений не ответило за 30 секунд';

export const READ_FAILURE_MESSAGES = {
  library: 'Не удалось загрузить список проектов за минуту. Повторите чтение.',
  recognition:
    'Не удалось получить состояние распознавания за минуту. Проверьте статус.',
  operation:
    'Не удалось получить состояние операции за минуту. Проверьте статус.',
  release:
    'Не удалось загрузить сохранённый пакет за минуту. Повторите чтение.',
} as const;

export const RUNTIME_UNAVAILABLE_MESSAGE =
  'Локальный сервис недоступен. Дождитесь подтверждённого перезапуска.';
export const HTTP_FAILURE_MESSAGE = (status: number) =>
  `Ошибка запроса: ${status}`;

export const EVENT_STREAM_FAILURE_MESSAGE =
  'Не удалось получить обновления задачи';
