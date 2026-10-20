/** Подготовка исходника: назначения слоёв, доступность локального расчёта и восстановление операций */
import { toLayerMapping } from '@/entities/source-data/model/layerKinds';
import type {
  Layer,
  LayerMapping,
  Project,
  ProjectOperation,
  ProjectRead,
} from '@green/api-client';
export const EMPTY_LAYERS: Layer[] = [];
export { projectSourceIdentity as sourceIdentity } from '@/entities/project/model/useProjectWrite';
export const layerMappings = (layers: Layer[]) =>
  Object.fromEntries(layers.map((layer) => [layer.id, toLayerMapping(layer)]));

/** Допуск интерфейса; целостность подготовленного снимка проверяет сервер перед расчётом */
export function canCalculatePreparedNative(
  project: ProjectRead | undefined,
  mappings: Readonly<Record<string, LayerMapping>>,
): boolean {
  if (
    project?.import_status?.mode !== 'autocad_live' ||
    !project.source_file?.native_session ||
    project.prepared_state !== 'available'
  )
    return false;

  const layers = project.layers ?? [];
  if (
    !layers.some(
      (layer) =>
        layer.mapping_review_required &&
        mappings[layer.id]?.confirmed === false,
    )
  )
    return false;

  const boundaries = layers.filter(
    (layer) => mappings[layer.id]?.kind === 'site_border',
  );
  return (
    boundaries.length > 0 &&
    boundaries.every(
      (layer) =>
        mappings[layer.id]?.confirmed === true &&
        layer.boundary_candidate?.status === 'usable',
    )
  );
}

import { operationActive } from '@/entities/operation/model/operationPresentation';
export { operationActive };
export const fromPreviousSource = (
  operation: ProjectOperation,
  project?: Project,
) =>
  Date.parse(operation.created_at ?? '') <
  Date.parse(project?.source_file?.imported_at ?? '');

/** Recover active work first; a later physical job must not hide a running calculation. */
export const latestPreparationOperation = (
  calculation: ProjectOperation | null,
  capture: ProjectOperation | null,
): ProjectOperation | null => {
  if (operationActive(calculation)) return calculation;
  if (operationActive(capture)) return capture;
  if (!calculation) return capture;
  if (!capture) return calculation;
  return Date.parse(capture.created_at ?? '') >
    Date.parse(calculation.created_at ?? '')
    ? capture
    : calculation;
};
