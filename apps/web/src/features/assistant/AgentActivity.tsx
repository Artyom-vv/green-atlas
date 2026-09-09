import { Check, ChevronDown, CircleAlert, LoaderCircle, Sparkles } from 'lucide-react';
import type { AgentTrace, AgentTraceEvent } from './assistantContext';

type PendingState = 'thinking' | 'preparing' | 'applying' | 'undoing';

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
  return typeof value === 'string' && value.trim() ? value.trim().replace(/\s*[·•]\s*/g, ' — ').slice(0, 80) : fallback;
}

function russianCount(value: number, one: string, few: string, many: string) {
  const mod100 = value % 100;
  const mod10 = value % 10;
  return `${value} ${mod100 >= 11 && mod100 <= 14 ? many : mod10 === 1 ? one : mod10 >= 2 && mod10 <= 4 ? few : many}`;
}

function eventCopy(event: AgentTraceEvent): { title: string; detail?: string; state: 'done' | 'recovered' } {
  const title = toolLabels[event.tool ?? ''] ?? 'Проверил данные';
  const summary = event.summary ?? {};
  if (event.ok === false) {
    return { title, detail: event.tool === 'prepare_placement' ? 'Вариант не подошёл, помощник продолжил поиск' : 'Шаг не подошёл, помощник продолжил', state: 'recovered' };
  }
  if (event.tool === 'select_zone_by_spatial_intent') {
    return { title, detail: safeText(summary.label, summary.anchor === 'edge' ? 'У края территории' : 'Подходящий участок'), state: 'done' };
  }
  if (event.tool === 'species_shortlist') {
    const total = typeof summary.total === 'number' ? summary.total : undefined;
    return { title, detail: total ? russianCount(total, 'вариант', 'варианта', 'вариантов') : 'Из доступного каталога', state: 'done' };
  }
  if (event.tool === 'prepare_placement') {
    if (summary.can_apply === true) {
      const found = typeof summary.found === 'number' ? ` (${russianCount(summary.found, 'место', 'места', 'мест')})` : '';
      return { title, detail: `Вариант проходит проверки${found}`, state: 'done' };
    }
    return { title, detail: 'Проверил вариант по правилам проекта', state: 'done' };
  }
  if (typeof summary.total === 'number') return { title, detail: russianCount(summary.total, 'объект', 'объекта', 'объектов'), state: 'done' };
  if (typeof summary.count === 'number') return { title, detail: russianCount(summary.count, 'объект', 'объекта', 'объектов'), state: 'done' };
  return { title, state: 'done' };
}

function pendingLabel(pending: PendingState) {
  if (pending === 'preparing') return 'Готовлю предложение';
  if (pending === 'applying') return 'Применяю подтверждённое изменение';
  if (pending === 'undoing') return 'Отменяю изменение';
  return 'Проверяю задачу';
}

export function AgentActivity({ trace, pending }: { trace?: AgentTrace; pending?: PendingState }) {
  const events = (trace?.events ?? []).filter(event => event && typeof event === 'object' && (event.tool || event.ok === false));
  if (pending) {
    return <div className="agent-activity__live" role="status" aria-live="polite">
      <span className="agent-activity__pulse" aria-hidden="true"><LoaderCircle size={14} /></span>
      <span>{pendingLabel(pending)}</span>
    </div>;
  }
  if (!events.length) return null;
  return <details className="agent-activity">
    <summary className="agent-activity__summary">
      <Sparkles size={14} aria-hidden="true" />
      <span>Ход работы</span>
      <span className="agent-activity__count">{events.length}</span>
      <ChevronDown size={14} aria-hidden="true" className="agent-activity__chevron" />
    </summary>
    <ol className="agent-activity__list">
      {events.map((event, index) => {
        const copy = eventCopy(event);
        return <li className="agent-activity__item" key={`${event.tool ?? 'step'}-${index}`}>
          <span className={`agent-activity__icon is-${copy.state}`} aria-hidden="true">{copy.state === 'done' ? <Check size={12} /> : <CircleAlert size={12} />}</span>
          <span className="agent-activity__body"><strong>{copy.title}</strong>{copy.detail ? <small>{copy.detail}</small> : null}</span>
        </li>;
      })}
    </ol>
  </details>;
}
