import type { AgentTraceEvent } from '@/features/assistant/model/assistantContext';
export type PendingState =
  'loading' | 'thinking' | 'preparing' | 'applying' | 'undoing';
const toolLabels: Record<string, string> = {
  project_context: 'Понял проект',
  select_zone_by_spatial_intent: 'Выбрал участок',
  inspect_zones: 'Проверил участки',
  find_plantings: 'Проверил посадки',
  inspect_plantings: 'Проверил посадки',
  species_catalog: 'Проверил каталог',
  building_targets: 'Проверил здания',
  road_targets: 'Проверил улицы',
  species_shortlist: 'Подобрал растения',
  data_passport: 'Проверил исходные данные',
  query_geometry: 'Проверил карту',
  growth_scene: 'Проверил рост',
  growth_objects: 'Проверил рост',
  plan_history: 'Проверил историю',
  plan_issues: 'Проверил ограничения',
  check_placement: 'Проверил место',
  read_result_page: 'Открыл данные',
  prepare_placement: 'Проверил размещение',
};
function safeText(value: unknown, fallback: string) {
  return typeof value === 'string' && value.trim()
    ? value
        .trim()
        .replace(/\s*[·•]\s*/g, ' — ')
        .slice(0, 80)
    : fallback;
}
function russianCount(value: number, one: string, few: string, many: string) {
  const mod100 = value % 100;
  const mod10 = value % 10;
  return `${value} ${mod100 >= 11 && mod100 <= 14 ? many : mod10 === 1 ? one : mod10 >= 2 && mod10 <= 4 ? few : many}`;
}
export function eventCopy(event: AgentTraceEvent): {
  title: string;
  detail?: string;
  state: 'done' | 'recovered';
} {
  const title = toolLabels[event.tool ?? ''] ?? 'Проверил данные';
  const summary = event.summary ?? {};
  if (event.ok === false) {
    return {
      title,
      detail:
        event.tool === 'prepare_placement'
          ? 'Вариант не подошёл, помощник продолжил поиск'
          : 'Шаг не подошёл, помощник продолжил',
      state: 'recovered',
    };
  }
  if (event.tool === 'select_zone_by_spatial_intent') {
    return {
      title,
      detail: safeText(
        summary.label,
        summary.anchor === 'edge' ? 'У края территории' : 'Подходящий участок',
      ),
      state: 'done',
    };
  }
  if (event.tool === 'species_shortlist') {
    const total = typeof summary.total === 'number' ? summary.total : undefined;
    return {
      title,
      detail: total
        ? russianCount(total, 'вариант', 'варианта', 'вариантов')
        : 'Из доступного каталога',
      state: 'done',
    };
  }
  if (event.tool === 'prepare_placement') {
    if (summary.can_apply === true) {
      const found =
        typeof summary.found === 'number'
          ? ` (${russianCount(summary.found, 'место', 'места', 'мест')})`
          : '';
      return {
        title,
        detail: `Вариант проходит проверки${found}`,
        state: 'done',
      };
    }
    return {
      title,
      detail: 'Проверил вариант по правилам проекта',
      state: 'done',
    };
  }
  if (typeof summary.total === 'number')
    return {
      title,
      detail: russianCount(summary.total, 'объект', 'объекта', 'объектов'),
      state: 'done',
    };
  if (typeof summary.count === 'number')
    return {
      title,
      detail: russianCount(summary.count, 'объект', 'объекта', 'объектов'),
      state: 'done',
    };
  return { title, state: 'done' };
}
export function pendingLabel(pending: PendingState) {
  if (pending === 'loading') return 'Открываем диалог…';
  if (pending === 'preparing') return 'Готовлю предложение';
  if (pending === 'applying') return 'Применяю подтверждённое изменение';
  if (pending === 'undoing') return 'Отменяю изменение';
  return 'Проверяю задачу';
}
