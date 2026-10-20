/** Ожидание чтения проекта сообщает о задержке и не ограничивает время запроса */
export const PROJECT_READ_NOTICE_DELAY_MS = 10_000;

export const PROJECT_READ_MESSAGES = {
  loading: 'Загрузка проекта',
  workspaceLoading: 'Загрузка рабочей области',
  opening: 'Открываем сохранённый проект',
  delayed: 'Чтение проекта занимает больше времени',
  failed: 'Не удалось прочитать проект',
  missing: 'Проект не найден',
  stopped: 'Чтение проекта остановлено',
  retry: 'Повторить',
  back: 'К проектам',
} as const;
