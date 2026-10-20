/** Исходные данные: представление физической роли и намерения замыкания границ */
import type {
  Layer,
  LayerCategory,
  LayerKind,
  LayerMapping,
  LayerRecognition,
} from '@green/api-client';
import { changeLayerRole, isBoundaryTopologyIntent } from '../model/layerKinds';
import { localLayerName } from '../model/boundaryDecision';

/** Категория описывает исходник, роль задаёт влияние физических объектов */
export function layerMappingPresentation(
  layer: Layer,
  mapping: LayerMapping,
  recognition?: LayerRecognition,
) {
  const categories = recognition?.categories ?? [];
  const proposal = recognition?.proposals.find(
    (item) => item.layer_id === layer.id,
  );
  const suggested = categories.find(
    (item) => item.category === proposal?.category,
  );
  const selected = categories.find(
    (item) => item.category === mapping.category,
  );
  const descriptive =
    selected?.kind === null
      ? selected
      : suggested?.kind === null
        ? suggested
        : undefined;
  const pending = mapping.confirmed === false;
  const topologyIntent = isBoundaryTopologyIntent(mapping);
  const objectRoles =
    mapping.kind === 'ignore' &&
    mapping.confirmed !== true &&
    (localLayerName(layer).trim() === '0' ||
      mapping.category === 'mixed_source' ||
      descriptive?.category === 'mixed_source');
  const legacyBoundaryChoice =
    layer.suggested_kind === 'site_border' && !layer.boundary_candidate;
  const suggestedRole =
    suggested?.kind &&
    suggested.kind !== 'site_border' &&
    suggested.kind !== 'ignore' &&
    proposal?.confidence !== 'low'
      ? suggested.kind
      : proposal?.calculation_role &&
          proposal.calculation_role !== 'ignore' &&
          proposal.calculation_role !== 'site_border' &&
          proposal.confidence !== 'low'
        ? proposal.calculation_role
        : !suggested &&
            proposal?.confidence !== 'low' &&
            mapping.kind !== 'ignore' &&
            mapping.kind !== 'site_border'
          ? mapping.kind
          : null;
  const boundary =
    mapping.kind === 'site_border' ||
    (pending && suggested?.kind === 'site_border');
  const category = selected ?? (pending ? suggested : undefined);
  const role = pending ? suggestedRole : mapping.kind;
  const chooseRole = (kind: LayerKind, explicitCategory?: LayerCategory) => {
    const candidate =
      explicitCategory === undefined
        ? (descriptive ?? selected ?? suggested)
        : categories.find((item) => item.category === explicitCategory);
    const matchingCategory =
      candidate?.kind === null || candidate?.kind === kind
        ? candidate.category
        : null;
    return changeLayerRole(mapping, kind, matchingCategory);
  };
  return {
    categories,
    proposal,
    suggested,
    selected,
    descriptive,
    pending,
    topologyIntent,
    objectRoles,
    suggestedRole,
    boundary,
    legacyBoundaryChoice,
    category,
    role,
    chooseRole,
  };
}

export function layerObjectCount(count: number) {
  const lastTwo = count % 100;
  const last = count % 10;
  const word =
    lastTwo >= 11 && lastTwo <= 14
      ? 'объектов'
      : last === 1
        ? 'объект'
        : last >= 2 && last <= 4
          ? 'объекта'
          : 'объектов';
  return `${count.toLocaleString('ru-RU')} ${word}`;
}

export function layerDisplayName(sourceName: string) {
  const parts = sourceName.split('|');
  if (parts.length === 1)
    return { name: sourceName, origin: 'Основной чертёж' };
  const source = parts.slice(0, -1).at(-1)?.split('_').at(-1) ?? '';
  const origin = /топограф/i.test(source)
    ? 'Топографическая подоснова'
    : /сети/i.test(source)
      ? 'Инженерная подоснова'
      : source;
  return { name: parts.at(-1) ?? sourceName, origin };
}
