# Выпуск и нижние результаты

Проверенный срез 14 сентября 2026: 80 профильных тестов и scoped ESLint.
После финальной декомпозиции совместный прогон с подготовкой источников,
запуском редактора и запросом карты дал 123 passed. В проверке TypeScript -b
ошибок этой области нет; основная задача завершает общий прогон.
Production/Storybook и визуальную приёмку выполняет основной агент на общей сборке.

## Владельцы

- `model/useReleaseDraftForm`: живые поля React Hook Form. Сериализуемые
  checkpoints сохраняют буквальные заметки и горизонт при закрытии окна,
  навигации между проектами и перезагрузке. Гидратация не является правкой.
- `model/releaseDraftStorage`: документ v1, ревизия, проектный gate отправки и
  receipt пакета. Существующие sessionStorage/localStorage ключи сохранены.
- `model/useProjectRelease`: контекст версии, применение checkpoint к запросу,
  получение пакета и восстановление. Серверный пакет живёт в React Query;
  в storage сохраняется только его ID.
- `api/releases`: проверка принадлежности ответа проекту и ID пакета.
- `ui/ReleasePanel`: поверхность и переход форма/файлы. `ReleaseForm` и
  `ReleaseBasisFields` используют FormProvider, FieldGroup, FieldGrid и контролы
  `@green/ui`; `ReleaseFiles` отвечает за сохранённый результат и скачивание.
  Самостоятельные группы вынесены в `ReleaseDraftStatus`, `ReleaseModeFields`,
  `ReleaseForecastField` и `ReleaseDownloads`. Новых владельцев состояния эти
  компоненты не создают: поля остаются в том же FormProvider.

RHF получает `formMethods: UseFormReturn<ReleaseFormValues>`. Параметры полей,
их ограничения, решения и подписи находятся в `model/releaseForm`; вычисление
готовности — чистая функция `releaseReadiness`. Парные поля переходят в одну
колонку при недостатке 220 px на каждое, кнопки не сжимаются.

## Границы операции

Черновик ID/revision/context фиксируется перед POST. Только совпадающий ответ
может потребить этот черновик. Изменившийся план или другой поздний ответ не
удаляют новые заметки. Синхронный проектный gate сохраняется через remount.

После известного ID receipt записывается **до** публикации Query. Ошибка
публикации блокирует ещё один POST; `retryRestore` читает тот же ID через GET.
При потерянном ответе создания ID неизвестен. Существующий API не имеет
idempotency key и поиска последнего пакета: UI явно сообщает неопределённость.
Открытие, reset и восстановление не повторяют POST; пользователь может явно
собрать новый неизменяемый пакет после предупреждения.

## Остальные результаты

- История: `entities/plan-history/ui`, команды в `features/plan-history`.
  Undo/Redo имеют общий gate. Потерянный ответ и ошибка после commit ведут к
  чтению project → history → project; версии должны совпасть и не быть старее
  известного кэша. Reset не отменяет неопределённую запись и не повторяет её.
  Реальная связка controller → manualWorkspaceWork проверяет блокировку ухода,
  сброса и помощника при read-recovery даже без ручного черновика.
- Проверки: `entities/validation`. Группировка назначения сохраняет различия
  вида и закрепления; идентификаторы обеих сторон конфликта доступны карте.
  Детальная ValidationPanel сохранена отдельно: у неё другое назначение.
  `CheckIssueGroup` владеет действиями группы; `CheckIssueRow` показывает
  отдельное нарушение. Идентификаторы строки вычисляются один раз.
- Ведомость: `entities/planting/model/schedule`, `ui/PlantingSchedule`.
  Группы различаются по типу и revision ID, а не только по видимой подписи.
- Review: `features/plan-changes/model/changeReviewSummary`,
  `ui/ChangeSetReviewPanel`. Escape/закрытие возвращают к осмотру, если он
  доступен; явная отмена остаётся отдельной командой.
  Влияние на план, причины и подробные ограничения имеют самостоятельные
  UI-компоненты; у них общий чистый результат `ChangeReviewSummary`.
- `shared/ui/results/ResultPanel` задаёт общий компактный заголовок, счётчик и
  перенос действий. Dock и его единственный scrollport остаются владельцами
  занимаемого пространства.

## Удалённое оформление и проверки

Удалены `workspace-results.css` (52 строки), `release-panel.css` (43 строки),
`change-review.css` (15 строк) и 58 заменённых правил из прежних общих CSS.
Все временные re-export этих компонентов из domain-ui удалены. Основная
страница подключена к новым владельцам. Корневой агент перенёс плавающий
change prompt из старого change-review.css в собственную Tailwind-композицию.

72 прежних теста сохранены; 8 новых проверяют двойную отправку, lost-response,
ошибку после подтверждения, read-recovery, смену проекта и монотонность кэша.
Условные JSX-узлы без альтернативы используют boolean &&; числовые счётчики
сравниваются с нулём, чтобы не вывести лишний «0». Истинные альтернативы
форма/файлы и список/пустое состояние сохраняют явное ветвление.
Тесты, проверявшие CSS-классы и нативный details, используют роли и поведение
общего Disclosure. Команды локально: `pnpm --filter @green/web exec vitest run
src/features/project-release src/features/plan-history src/entities/plan-history
src/entities/validation src/entities/planting/ui/PlantingSchedule.test.tsx
src/features/plan-changes/ui/ChangeSetReviewPanel.test.tsx`.

Применены SRP (форма/хранение/серверный результат), композиция (поле/группа/
поверхность), DRY (общая группировка/границы полей), функциональные вычисления
и YAGNI (без нового конструкторa форм или API). Эффекты ограничены синхронизацией
формы, browser checkpoint и внешнего Query-состояния; расчёты остаются функциями.
Основания: [React — эффекты и вычисляемые значения](https://react.dev/learn/you-might-not-need-an-effect),
[исходный контракт RHF useForm](https://github.com/react-hook-form/react-hook-form/blob/master/src/useForm.ts),
[React Query — мутации](https://tanstack.com/query/latest/docs/framework/react/guides/mutations).
