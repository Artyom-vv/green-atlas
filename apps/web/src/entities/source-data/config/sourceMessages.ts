/** Сообщения чтения проекта, подготовки и распознавания не определяют состояние задач. */
export const PREPARATION_MESSAGES = {
  incomplete: 'Подготовка ещё не завершена',
  unconfirmed: 'Результат подготовки не подтверждён',
  readingDrawing: 'Читаем подготовленный чертёж',
  openingMap: 'Открываем подготовленную карту',
  drawingReadFailed: 'Не удалось прочитать подготовленный чертёж',
  mapOpenFailed: 'Не удалось открыть подготовленную карту',
  retryDrawingRead: 'Повторить чтение чертежа',
  retryMapOpen: 'Повторить открытие карты',
  mappingsReadRecovery: 'Назначения слоёв сохранены, повторите чтение проекта',
  mappingsSaveUnknown:
    'Сохранение назначений не подтверждено, проверьте результат',
  projectReadRecovery: 'Не удалось прочитать проект, повторите чтение',
  recoveryTitle: 'Восстановление подготовки',
  checkProject: 'Проверить состояние проекта',
  unknownStatus: 'Статус подготовки недоступен',
  lastStage: 'Последний этап',
  retryStatusRead: 'Обновить статус подготовки',
  checkingStatus: 'Проверяем состояние подготовки карты',
  previousDrawing: 'Завершается подготовка предыдущего чертежа',
  preparingDrawing: 'Подготовка чертежа',
  preparingMap: 'Подготовка карты',
  requiredBoundaryMapping: 'Назначьте роли обязательным слоям границы',
  reviewMappings: 'Проверьте предложенные роли слоёв',
  unavailableMap: 'Для редактирования нужна подготовка карты',
  partialMap: 'Карта строится по доступным данным',
  readingPassport: 'Загружаем сведения об исходных данных',
  passportReadFailed: 'Не удалось загрузить сведения об исходных данных',
} as const;

export const RECOGNITION_MESSAGES = {
  phases: {
    checking: {
      title: 'Проверяем состояние распознавания',
    },
    unknown: {
      title: 'Статус распознавания неизвестен',
    },
    loading: {
      title: 'Получаем состояние распознавания',
    },
    not_started: {
      title: 'Распознавание не началось',
    },
    resuming: {
      title: 'Запускаем распознавание',
    },
    queued: {
      title: 'Готовим распознавание',
    },
    classifying: {
      title: 'Распознаём слои',
    },
    reviewing: {
      title: 'Уточняем спорные назначения',
    },
    finishing: {
      title: 'Ожидаем результат распознавания',
    },
    publishing: {
      title: 'Сохраняем предложения',
    },
    saving: {
      title: 'Сохраняем назначения слоёв',
    },
    completed: {
      title: 'Распознавание завершено',
    },
    failed: {
      title: 'Распознавание приостановлено',
    },
    unconfigured: {
      title: 'Распознавание не подключено',
    },
  },
  assignmentsSaved: 'Назначения слоёв сохранены',
  waitingForModel: 'Ждём ответ модели',
  reconnecting: 'Восстанавливаем соединение',
  resumeMessage: 'Продолжаем с сохранённого результата',
  review: 'Проверить слои',
  openLayers: 'Открыть слои',
  details: 'Подробности',
  duration: 'Длительность',
  indicator: {
    checking: 'Проверяем статус',
    unknown: 'Статус недоступен',
    loading: 'Получаем статус',
    not_started: 'Распознавание не начато',
    resuming: 'Запускаем распознавание',
    queued: 'Готовим распознавание',
    classifying: 'Распознаём',
    reviewing: 'Уточняем',
    finishing: 'Ожидаем результат',
    publishing: 'Сохраняем результат',
    saving: 'Сохраняем слои',
    completed: 'Слои распознаны',
    failed: 'Распознавание остановлено',
    unconfigured: 'Распознавание недоступно',
  },
  check: 'Проверяем…',
  checkAction: 'Обновить статус',
  startAction: 'Запустить распознавание',
  starting: 'Запускаем…',
  resuming: 'Продолжаем…',
  resumeAction: 'Продолжить',
  retryAction: 'Повторить',
  primaryCount: 'Первый проход',
  elapsed: 'Прошло',
  layerTask: (name: string) => `Слой ${name}`,
  moreLayers: (count: number) => `, ещё ${count}`,
} as const;

export type RecognitionPhase = keyof typeof RECOGNITION_MESSAGES.phases;
