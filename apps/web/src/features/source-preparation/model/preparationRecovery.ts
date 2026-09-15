import type {
  LayerMapping,
  Project,
  ProjectOperation,
} from '@green/api-client';
import {
  layerMappings,
  operationActive,
  sourceIdentity,
} from './sourcePreparation';

export interface PreparationAttempt {
  projectId: string;
  source: string;
  mappings: LayerMapping[];
  draftKey: string;
  baseStateVersion: number;
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
  getLatestOperation: (
    projectId: string,
    kind: 'calculate_geometry',
  ) => Promise<ProjectOperation | null>;
}

export class UnconfirmedPreparation extends Error {}

export const mappingKey = (mappings: LayerMapping[]) =>
  JSON.stringify(
    mappings
      .map(({ layer_id, kind, visible }) => [layer_id, kind, visible ?? true])
      .sort((left, right) => String(left[0]).localeCompare(String(right[0]))),
  );

export function mappingReceipt(
  attempt: PreparationAttempt,
  project: Project,
  evidence: MappingReceipt['evidence'] = 'save_response',
): MappingReceipt {
  if (!Number.isInteger(project.state_version))
    throw new UnconfirmedPreparation('Сервер не подтвердил версию настроек.');
  return {
    evidence,
    source: attempt.source,
    draftKey: attempt.draftKey,
    stateVersion: project.state_version!,
    geometryVersion: project.geometry_version ?? 0,
  };
}

/** Reads cannot prove ownership of a lost write. They can prove the current
 * source, mapping values and the exact version a geometry operation uses. */
export async function readPreparationEvidence(
  port: PreparationReadPort,
  attempt: PreparationAttempt,
  receipt?: MappingReceipt,
) {
  const before = await port.getProject(attempt.projectId, false);
  const operation = await port.getLatestOperation(
    attempt.projectId,
    'calculate_geometry',
  );
  const project = await port.getProject(attempt.projectId, false);
  if (
    before.state_version !== project.state_version ||
    sourceIdentity(before) !== sourceIdentity(project)
  )
    throw new UnconfirmedPreparation(
      'Проект изменился во время проверки. Прочитайте состояние ещё раз.',
    );
  if (
    project.id !== attempt.projectId ||
    sourceIdentity(project) !== attempt.source ||
    mappingKey(Object.values(layerMappings(project.layers ?? []))) !==
      attempt.draftKey
  )
    throw new UnconfirmedPreparation(
      'Исходник или настройки изменились. Обновите проект перед новой подготовкой.',
    );
  if (!receipt && (project.state_version ?? 0) <= attempt.baseStateVersion)
    throw new UnconfirmedPreparation(
      'Не удалось подтвердить сохранение настроек. Проверяем актуальное состояние проекта.',
    );
  const confirmed =
    receipt ?? mappingReceipt(attempt, project, 'current_snapshot');
  const matching =
    operation?.project_id === attempt.projectId &&
    operation.kind === 'calculate_geometry' &&
    operation.project_state_version === confirmed.stateVersion;
  if (matching && operation.status === 'completed') {
    if (
      !project.map_ready ||
      (project.geometry_version ?? 0) <= confirmed.geometryVersion
    )
      throw new UnconfirmedPreparation(
        'Расчёт завершён, но подготовленная карта ещё не подтверждена свежим снимком.',
      );
    return { project, receipt: confirmed, operation };
  }
  if (project.state_version !== confirmed.stateVersion)
    throw new UnconfirmedPreparation(
      'Версия проекта изменилась. Обновите проект перед продолжением.',
    );
  if (operationActive(operation) && !matching)
    throw new UnconfirmedPreparation(
      'Выполняется расчёт другой версии настроек. Дождитесь его завершения и прочитайте состояние.',
    );
  return {
    project,
    receipt: confirmed,
    operation: matching ? operation : null,
  };
}
