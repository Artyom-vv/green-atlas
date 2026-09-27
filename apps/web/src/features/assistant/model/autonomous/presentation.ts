import { type AgentRun, type AgentRunEvent } from '@green/api-client';
export type Data = Record<string, unknown>;

export interface Capacity {
  status: 'exact' | 'partial' | 'impossible';
  found: number;
  requested?: number;
  shortfall: number;
  reason?: string;
  remedies: Data[];
}

export interface SpeciesOption {
  id?: string;
  name: string;
  scientificName?: string;
  status?: string;
  reasons: string[];
}

export interface Recovery {
  projectId: string;
  runId: string;
  action: 'answer' | 'approve' | 'execute' | 'resume' | 'cancel';
  revision: number;
  errorMessage?: string;
}

export interface RestoreFailure {
  projectId: string;
  runId: string;
  message: string;
}

export const toolLabels: Record<string, string> = {
  find_zone_candidates: 'Подбор участка',
  inspect_zones: 'Проверка участка',
  species_shortlist: 'Подбор растений',
  building_targets: 'Проверка зданий',
  road_targets: 'Проверка улиц',
  prepare_placement: 'Расчёт размещения',
  prepare_existing_change: 'Подготовка изменения',
  growth_scene: 'Проверка роста',
  growth_objects: 'Проверка роста',
  plan_issues: 'Проверка ограничений',
  prepare_zone_change: 'Проверка изменения участка',
  commit_zone_change: 'Сохранение участка',
  focus_zone: 'Показ участка на карте',
};

export const starters = [
  {
    label: 'Разместить деревья',
    text: 'Посади 10 деревьев вдоль зданий. Участок и породу выбери сам.',
  },
  {
    label: 'Подобрать породы',
    text: 'Подбери подходящие породы деревьев для участков проекта.',
  },
  {
    label: 'Проверить план',
    text: 'Проверь текущий план посадок и покажи найденные ограничения.',
  },
];

export const arrangementLabels: Record<string, string> = {
  area: 'По площади участка',
  building_contour: 'Вдоль зданий',
  building_groves: 'Группами у зданий',
  road_edges: 'Вдоль улиц',
};

export const record = (value: unknown): Data | undefined =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Data)
    : undefined;

export const count = (value: unknown) =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0
    ? value
    : undefined;

export function errorText(error: unknown) {
  return error instanceof Error
    ? error.message
    : 'Не удалось продолжить запуск.';
}

export function capacity(data: Data | undefined): Capacity | undefined {
  if (!data) return undefined;
  const outcome = record(data.placement_outcome);
  const proposal = record(data.proposal);
  const found = count(outcome?.found ?? data.found ?? proposal?.found);
  if (found === undefined) return undefined;
  const requested = count(
    outcome?.requested ?? data.requested ?? proposal?.requested,
  );
  const shortfall = Math.max(
    count(outcome?.shortfall ?? data.shortfall ?? proposal?.shortfall) ?? 0,
    requested === undefined ? 0 : requested - found,
  );
  // Legacy or inconsistent statuses cannot turn an incomplete result into approval.
  const status =
    found === 0 || outcome?.status === 'impossible'
      ? 'impossible'
      : shortfall > 0 || outcome?.status === 'partial'
        ? 'partial'
        : 'exact';
  const reason =
    outcome?.reason ??
    data.shortfall_explanation ??
    proposal?.shortfall_explanation;
  return {
    status,
    found,
    requested,
    shortfall,
    reason: typeof reason === 'string' ? reason : undefined,
    remedies: Array.isArray(outcome?.remedy_options)
      ? outcome.remedy_options
          .map(record)
          .filter((item): item is Data => Boolean(item))
      : [],
  };
}

export function attemptEvents(run: AgentRun | undefined) {
  const events = run?.events ?? [];
  const reset = [...events]
    .reverse()
    .find((event) =>
      ['run_restarted', 'question_answered'].includes(event.kind),
    );
  return reset
    ? events.filter((event) => event.sequence >= reset.sequence)
    : events;
}

export function currentResult(run: AgentRun | undefined) {
  const current = attemptEvents(run).filter(
    (event) => event.kind === 'tool_result',
  );
  const last = run?.state.last_result;
  const restarted = attemptEvents(run).some((event) =>
    ['run_restarted', 'question_answered'].includes(event.kind),
  );
  return last &&
    (!restarted ||
      current.some((event) => event.payload.call_id === last.call_id))
    ? last
    : current.at(-1)?.payload;
}

export function readSummary(
  run: AgentRun | undefined,
  result: Data | undefined,
) {
  const page = record(result?.read_page);
  const outcome = record(run?.state.read_outcome);
  const complete =
    page !== undefined &&
    outcome !== undefined &&
    run?.state.status === 'finished' &&
    outcome.status === 'complete' &&
    outcome.capability === result?.name &&
    page.capability === outcome.capability &&
    page.source === outcome.source &&
    page.total === outcome.total &&
    page.snapshot_version === outcome.snapshot_version &&
    page.plan_version === outcome.plan_version;
  const evidence = complete ? outcome : page;
  const source =
    evidence?.source === 'saved_plan_validation'
      ? 'Сохранённая проверка плана'
      : evidence?.source === 'species_suitability'
        ? 'Подбор по данным участка'
        : undefined;
  return {
    total: count(evidence?.total),
    pages: complete ? count(outcome.pages) : undefined,
    offset: count(page?.offset),
    source,
    zoneIds: Array.isArray(evidence?.zone_ids)
      ? evidence.zone_ids.filter((id): id is string => typeof id === 'string')
      : [],
    caveat:
      typeof evidence?.caveat === 'string'
        ? evidence.caveat
            .replace(/\bavailable\b/gi, '«доступна»')
            .replace(/\breview\b/gi, '«нужна проверка»')
            .replace(/проверенный preview/gi, 'проверенное предложение')
            .replace(/\bpreview\b/gi, 'предложение')
        : undefined,
  };
}

export function readExcerpt(
  total: number,
  shown: number,
  read: ReturnType<typeof readSummary>,
) {
  return shown && ((read.pages ?? 0) > 1 || (read.offset ?? 0) > 0)
    ? `Выдержка последней страницы: записи ${(read.offset ?? 0) + 1}–${(read.offset ?? 0) + shown} из ${total}.`
    : undefined;
}

export function placementData(run: AgentRun | undefined) {
  const last = run?.state.last_result;
  if (
    last?.name === 'prepare_placement' ||
    record(last?.data)?.placement_outcome
  )
    return record(last?.data);
  return record(
    [...attemptEvents(run)]
      .reverse()
      .find(
        (event) =>
          event.kind === 'tool_result' &&
          event.payload.name === 'prepare_placement',
      )?.payload.data,
  );
}

export function shortlistData(value: unknown) {
  const data = record(value);
  const raw = Array.isArray(value)
    ? value
    : Array.isArray(data?.items)
      ? data.items
      : undefined;
  if (!raw) return undefined;
  const items = raw.flatMap((value) => {
    const item = record(value);
    const species = record(item?.species);
    if (typeof species?.common_name !== 'string' || !species.common_name.trim())
      return [];
    return [
      {
        id: typeof species.id === 'string' ? species.id : undefined,
        name: species.common_name,
        scientificName:
          typeof species.scientific_name === 'string'
            ? species.scientific_name
            : undefined,
        status: typeof item?.status === 'string' ? item.status : undefined,
        reasons: Array.isArray(item?.reasons)
          ? item.reasons.filter(
              (reason): reason is string => typeof reason === 'string',
            )
          : [],
      },
    ];
  });
  return { items, total: count(data?.total) ?? items.length };
}

export function latestShortlist(run: AgentRun | undefined) {
  const result = currentResult(run);
  const shortlist =
    result?.name === 'species_shortlist' && result.status === 'succeeded'
      ? shortlistData(result.data)
      : undefined;
  const read = readSummary(run, result);
  return shortlist
    ? { ...shortlist, total: read.total ?? shortlist.total, read }
    : undefined;
}

export function shortlistNames(run: AgentRun | undefined) {
  const sources = [
    ...attemptEvents(run)
      .filter((event) => event.kind === 'tool_result')
      .map((event) => event.payload),
    currentResult(run),
  ];
  return new Map(
    sources.flatMap((result) =>
      result?.name === 'species_shortlist' && result.status === 'succeeded'
        ? (shortlistData(result.data)?.items ?? []).flatMap((species) =>
            species.id ? [[species.id, species.name] as const] : [],
          )
        : [],
    ),
  );
}

export function savedIssues(run: AgentRun | undefined) {
  const last = currentResult(run);
  const result =
    last?.name === 'plan_issues'
      ? last
      : [...attemptEvents(run)]
          .reverse()
          .find(
            (event) =>
              event.kind === 'tool_result' &&
              event.payload.name === 'plan_issues',
          )?.payload;
  const data = record(result?.data);
  const read = readSummary(run, result);
  const total = read.total ?? count(data?.total);
  if (
    result?.status !== 'succeeded' ||
    data?.source !== 'saved_plan_validation' ||
    total === undefined
  )
    return undefined;
  const items = Array.isArray(data.items)
    ? data.items
        .map(record)
        .filter((item): item is Data => typeof item?.title === 'string')
        .slice(0, 5)
    : [];
  return { total, items, planVersion: count(data.plan_version), read };
}

export function issueMeasurement(issue: Data) {
  const unit = typeof issue.unit === 'string' ? ` ${issue.unit}` : '';
  return [
    ['Фактически', issue.actual],
    ['Требуется', issue.required],
  ]
    .flatMap(([label, value]) =>
      typeof value === 'number' && Number.isFinite(value)
        ? [`${label}: ${value.toLocaleString('ru-RU')}${unit}`]
        : [],
    )
    .join(', ');
}

export function capacityText(result: Capacity) {
  return result.requested === undefined
    ? `Найдено мест: ${result.found}`
    : `Найдено ${result.found} из ${result.requested} мест`;
}

export function payloadText(event: AgentRunEvent) {
  const data = record(event.payload.data);
  const name = event.payload.name;
  if (name === 'find_zone_candidates' && typeof data?.total === 'number')
    return `${data.total} участков проверено`;
  if (name === 'species_shortlist' && typeof data?.total === 'number')
    return `${data.total} вариантов в каталоге`;
  if (name === 'prepare_placement') {
    const result = capacity(data);
    if (result) return capacityText(result);
  }
  return undefined;
}

export function failureText(run: AgentRun | undefined) {
  const code = run?.state.failure?.code;
  if (code === 'APPROVAL_OUTCOME_UNKNOWN')
    return 'Не удалось подтвердить результат сохранения. Проверьте изменения на карте и историю проекта перед новой задачей.';
  if (code === 'AGENT_NO_PROGRESS')
    return 'Шаги повторились без нового результата. Измените способ размещения.';
  if (code === 'INVALID_TOOL_ARGUMENTS')
    return 'Один из шагов получил неполные параметры. Запустите задачу ещё раз.';
  if (code === 'STALE_PROJECT')
    return 'Проект изменился во время расчёта. Запустите задачу заново.';
  return typeof run?.state.failure?.message === 'string'
    ? run.state.failure.message
    : 'Предложение не подготовлено. Измените задачу и повторите расчёт.';
}

export function eventPresentation(event: AgentRunEvent, incomplete: boolean) {
  const payload = event.payload;
  const name = typeof payload.name === 'string' ? payload.name : '';
  const code = payload.code ?? record(payload.error)?.code;
  const result =
    name === 'prepare_placement' ? capacity(record(payload.data)) : undefined;
  const failed =
    ['failed', 'blocked', 'stale', 'cancelled'].includes(
      String(payload.status),
    ) || event.kind === 'run_failed';
  const partial =
    payload.status === 'partial' ||
    (result && result.status !== 'exact') ||
    (incomplete && event.kind === 'approval_requested');
  const done =
    !failed &&
    !partial &&
    (event.kind === 'commit_applied' ||
      (event.kind === 'tool_result' && payload.status === 'succeeded'));
  const title =
    event.kind === 'approval_requested'
      ? incomplete
        ? 'Предложение требует уточнения'
        : 'Предложение готово'
      : event.kind === 'commit_applied'
        ? 'Изменение применено'
        : event.kind === 'question'
          ? 'Нужен ваш ответ'
          : event.kind === 'run_started'
            ? 'Задача принята'
            : event.kind === 'run_restarted' || event.kind === 'run_resumed'
              ? 'Начат новый расчёт'
              : event.kind === 'question_answered'
                ? 'Задание уточнено'
                : event.kind === 'run_cancelled'
                  ? 'Запуск остановлен'
                  : event.kind === 'scope_fallback_started'
                    ? 'Проверка другого участка'
                    : event.kind === 'run_failed'
                      ? 'Расчёт остановлен'
                      : (toolLabels[name] ?? 'Проверка данных');
  const detail =
    code === 'REPEATED_TOOL_CALL'
      ? 'Шаг уже выполнялся — нужна другая стратегия'
      : code === 'INVALID_TOOL_ARGUMENTS'
        ? 'Параметры шага не подошли'
        : code === 'AGENT_NO_PROGRESS'
          ? 'Шаги повторились без нового результата'
          : failed
            ? 'Шаг не завершён'
            : payloadText(event);
  return {
    title,
    detail,
    tone: failed ? 'error' : partial ? 'partial' : done ? 'done' : 'neutral',
  };
}

export function currentActivity(run: AgentRun) {
  if (run.state.status === 'waiting_ui')
    return 'Ожидаю завершения движения карты';
  const latest = [...attemptEvents(run)]
    .reverse()
    .find((event) => event.kind === 'decision' || event.kind === 'tool_result');
  const tool = record(latest?.payload.tool);
  const name = latest?.kind === 'decision' ? tool?.name : latest?.payload.name;
  const label = typeof name === 'string' ? toolLabels[name] : undefined;
  return label
    ? `${latest?.kind === 'tool_result' ? 'Последний шаг: ' : ''}${label}`
    : 'Планирую следующий шаг';
}

export function runStatus(
  run: AgentRun | undefined,
  result: Capacity | undefined,
) {
  if (!run) return 'Готов принять задачу';
  if (run.state.status === 'queued') return 'Готов к продолжению';
  if (run.state.status === 'scheduled') return 'Задача в очереди';
  if (run.state.status === 'running') return 'Работаю над задачей';
  if (run.state.status === 'waiting_ui') return 'Показываю участок на карте';
  if (run.state.status === 'failed')
    return run.state.failure?.code === 'APPROVAL_OUTCOME_UNKNOWN'
      ? 'Нужно проверить результат сохранения'
      : run.state.failure?.retryable === false
        ? 'Расчёт остановлен'
        : 'Нужен повторный расчёт';
  if (run.state.status === 'cancelled') return 'Запуск остановлен';
  if (result?.status === 'partial') return 'Нужно уточнить задание';
  if (result?.status === 'impossible') return 'Подходящих мест не найдено';
  if (run.state.status === 'waiting_approval') return 'Предложение готово';
  if (run.state.status === 'waiting_question') return 'Нужен ваш ответ';
  if (run.state.status === 'finished') return 'Работа завершена';
  return 'Ожидаю результат расчёта';
}

export function remedyDraft(remedy: Data) {
  if (remedy.code === 'reduce_quantity' && count(remedy.target_count))
    return `Измени количество растений на ${remedy.target_count}. Сохрани участок, схему и породы.`;
  if (remedy.code === 'change_scope') return 'Используй другой участок: ';
  if (remedy.code === 'change_arrangement')
    return 'Измени схему размещения на ';
  if (remedy.code === 'change_species')
    return 'Подбери другие подходящие породы. Сохрани количество и участок.';
  return '';
}
