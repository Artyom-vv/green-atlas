import type {
  LayerMapping,
  Project,
  ProjectRead,
  ProjectOperation,
} from '@green/api-client';
import {
  layerMappings,
  operationActive,
  sourceIdentity,
} from './sourcePreparation';
import { canonicalMappingValue } from './canonicalMappingValue';

export interface PreparationAttempt {
  projectId: string;
  source: string;
  mappings: LayerMapping[];
  writeMappings?: LayerMapping[];
  draftKey: string;
  baseStateVersion: number;
  mode?: 'prepare' | 'capture' | 'save' | 'continue';
}

export interface MappingReceipt {
  evidence: 'save_response' | 'current_snapshot';
  source: string;
  draftKey: string;
  stateVersion: number;
  geometryVersion: number;
}

export interface PreparationReadPort {
  getProject: (projectId: string, includeGeometry: boolean) => Promise<Project>;
  getProjectRead: (
    projectId: string,
    includeGeometry: boolean,
  ) => Promise<ProjectRead>;
  getLatestOperation: (
    projectId: string,
    kind: 'calculate_geometry' | 'prepare_cad_capture',
  ) => Promise<ProjectOperation | null>;
}

export class UnconfirmedPreparation extends Error {}

export const canContinuePreparedProject = (project: Project) =>
  project.map_ready &&
  !project.source_review &&
  'prepared_state' in project &&
  ['available', 'not_required'].includes(String(project.prepared_state));

export const mappingKey = (mappings: LayerMapping[]) =>
  JSON.stringify(
    mappings
      .map(
        ({
          layer_id,
          kind,
          visible,
          category,
          confirmed,
          utility_context,
          utility_axis_bindings,
        }) => [
          layer_id,
          kind,
          visible ?? true,
          category ?? null,
          confirmed === undefined ? true : confirmed,
          utility_context ?? null,
          utility_axis_bindings ?? [],
        ],
      )
      .sort((left, right) => String(left[0]).localeCompare(String(right[0])))
      .map(canonicalMappingValue),
  );

export function mappingReceipt(
  attempt: PreparationAttempt,
  project: Project,
  evidence: MappingReceipt['evidence'] = 'save_response',
): MappingReceipt {
  if (
    project.id !== attempt.projectId ||
    sourceIdentity(project) !== attempt.source ||
    !Number.isSafeInteger(project.state_version) ||
    project.state_version < attempt.baseStateVersion
  )
    throw new UnconfirmedPreparation('Сервер не подтвердил версию настроек.');
  return {
    evidence,
    source: attempt.source,
    draftKey: attempt.draftKey,
    stateVersion: project.state_version!,
    geometryVersion: project.geometry_version ?? 0,
  };
}

/** Чтение подтверждает текущий исходник, назначения и версию расчёта,
 * но не авторство записи, чей ответ был потерян */
export async function readPreparationEvidence(
  port: PreparationReadPort,
  attempt: PreparationAttempt,
  receipt?: MappingReceipt,
) {
  if (attempt.mode === 'continue') {
    const project = await port.getProjectRead(attempt.projectId, false);
    if (
      project.id !== attempt.projectId ||
      sourceIdentity(project) !== attempt.source ||
      mappingKey(Object.values(layerMappings(project.layers ?? []))) !==
        attempt.draftKey ||
      project.state_version <
        (receipt?.stateVersion ?? attempt.baseStateVersion)
    )
      throw new UnconfirmedPreparation(
        'Актуальный проект не подтверждает сохранённые настройки. Повторите чтение; запись не повторяется',
      );
    return {
      project,
      receipt: receipt ?? mappingReceipt(attempt, project, 'current_snapshot'),
      operation: null,
    };
  }
  const readProject = () =>
    attempt.mode === 'capture' || attempt.mode === 'save'
      ? port.getProjectRead(attempt.projectId, false)
      : port.getProject(attempt.projectId, false);
  const kind =
    attempt.mode === 'capture' ? 'prepare_cad_capture' : 'calculate_geometry';
  const before = await readProject();
  const operation =
    attempt.mode === 'save'
      ? null
      : await port.getLatestOperation(attempt.projectId, kind);
  const project = attempt.mode === 'save' ? before : await readProject();
  if (
    before.state_version !== project.state_version ||
    sourceIdentity(before) !== sourceIdentity(project)
  )
    throw new UnconfirmedPreparation(
      'Проект изменился во время проверки. Прочитайте состояние ещё раз',
    );
  if (
    project.id !== attempt.projectId ||
    sourceIdentity(project) !== attempt.source ||
    mappingKey(Object.values(layerMappings(project.layers ?? []))) !==
      attempt.draftKey
  )
    throw new UnconfirmedPreparation(
      'Исходник или настройки изменились. Обновите проект перед новой подготовкой',
    );
  if (
    !receipt &&
    (project.state_version ?? 0) <
      attempt.baseStateVersion + (attempt.mode === 'save' ? 0 : 1)
  )
    throw new UnconfirmedPreparation(
      'Не удалось подтвердить сохранение настроек. Проверяем актуальное состояние проекта',
    );
  const confirmed =
    receipt ?? mappingReceipt(attempt, project, 'current_snapshot');
  if (attempt.mode === 'save') {
    if (project.state_version < confirmed.stateVersion)
      throw new UnconfirmedPreparation(
        'Прочитана прежняя версия сохранённых настроек',
      );
    return { project, receipt: confirmed, operation: null };
  }
  const matching =
    operation?.project_id === attempt.projectId &&
    operation.kind === kind &&
    operation.project_state_version === confirmed.stateVersion;
  if (attempt.mode === 'capture' && !matching)
    throw new UnconfirmedPreparation(
      'Запуск подготовки чертежа не подтверждён. Повторите чтение; подготовка повторно не запускается',
    );
  if (matching && operation.status === 'completed') {
    if (attempt.mode === 'capture') {
      if (!(
        'prepared_state' in project && project.prepared_state === 'available'
      ))
        throw new UnconfirmedPreparation(
          'Подготовленный снимок пока недоступен. Повторите чтение',
        );
      return { project, receipt: confirmed, operation };
    }
    if (
      !project.map_ready ||
      (project.geometry_version ?? 0) <= confirmed.geometryVersion
    )
      throw new UnconfirmedPreparation(
        'Расчёт завершён, но подготовленная карта ещё не подтверждена свежим снимком',
      );
    return { project, receipt: confirmed, operation };
  }
  if (project.state_version !== confirmed.stateVersion)
    throw new UnconfirmedPreparation(
      'Версия проекта изменилась. Обновите проект перед продолжением',
    );
  if (operationActive(operation) && !matching)
    throw new UnconfirmedPreparation(
      'Выполняется расчёт другой версии настроек. Дождитесь его завершения и прочитайте состояние',
    );
  return {
    project,
    receipt: confirmed,
    operation: matching ? operation : null,
  };
}
