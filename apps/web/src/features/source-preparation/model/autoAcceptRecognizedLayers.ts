/** Исходные данные: принятие подтверждённых предложений без потери спорных объектов */
import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
import { annotationOnly } from '@/entities/source-data/model/annotationContent';
import { completedRecognitionLayerIds } from '@/entities/source-data/model/recognitionProgress';
import {
  changeLayerRole,
  selectPlanningBoundary,
} from '@/entities/source-data/model/layerKinds';
import {
  isWorkBoundaryName,
  localLayerName,
  recommendedWorkBoundary,
} from '@/entities/source-data/model/boundaryDecision';

// Допустимый контекст совпадает с native-политикой CAD_REFERENCE на сервере.
const referenceContextTypes = new Set([
  'INSERT',
  'ATTRIB',
  'ATTDEF',
  'TEXT',
  'MTEXT',
  'DIMENSION',
  'LEADER',
  'MLEADER',
  'AUTOCAD:AcDbMLeader',
  'AUTOCAD:AcDbRasterImage',
]);

/** Исключать расчётную роль только при полном native-составе контекстных объектов */
function nativeReferenceOnly(
  layer: Layer,
  proposal: LayerRecognition['proposals'][number],
): boolean {
  if (
    proposal.category !== 'cad_reference' ||
    proposal.confidence !== 'high' ||
    (proposal.calculation_role && proposal.calculation_role !== 'ignore') ||
    proposal.unresolved.length > 0 ||
    proposal.review_question ||
    (proposal.review_roles?.length ?? 0) > 0 ||
    layer.geometry_complete !== true ||
    layer.unreadable_geometry_count !== 0 ||
    !layer.unsupported_geometry_types ||
    Object.keys(layer.unsupported_geometry_types).length > 0 ||
    !layer.projected_geometry_types ||
    Object.keys(layer.projected_geometry_types).length > 0 ||
    !layer.entity_types ||
    !layer.context_entity_types ||
    !Number.isSafeInteger(layer.object_count) ||
    layer.object_count <= 0
  )
    return false;
  const entities = Object.entries(layer.entity_types);
  const context = layer.context_entity_types;
  return (
    entities.length === Object.keys(context).length &&
    entities.some(([kind]) =>
      ['INSERT', 'ATTRIB', 'ATTDEF', 'AUTOCAD:AcDbRasterImage'].includes(kind),
    ) &&
    entities.every(
      ([kind, count]) =>
        referenceContextTypes.has(kind) &&
        Number.isSafeInteger(count) &&
        count > 0 &&
        context[kind] === count,
    ) &&
    entities.reduce((total, [, count]) => total + count, 0) ===
      layer.object_count
  );
}

/** Принимать обоснованные назначения исходника, сохраняя спорные объекты для проверки */
export function autoAcceptRecognizedLayers(
  layers: Layer[],
  mappings: Record<string, LayerMapping>,
  recognition?: LayerRecognition,
  preview = false,
): Record<string, LayerMapping> {
  if (
    !recognition ||
    (!['completed', 'failed'].includes(recognition.status) &&
      !(preview && recognition.status === 'running'))
  )
    return mappings;
  const completed = completedRecognitionLayerIds(layers, recognition);
  if (completed && !completed.size) return mappings;
  const categoryById = new Map(
    recognition.categories.map((item) => [item.category, item]),
  );
  const proposalById = new Map(
    recognition.proposals.map((item) => [item.layer_id, item]),
  );
  let next = { ...mappings };
  const workBoundary = recommendedWorkBoundary(layers);
  for (const layer of layers) {
    if (completed && !completed.has(layer.id)) continue;
    const mapping = mappings[layer.id];
    const proposal = proposalById.get(layer.id);
    if (!mapping || mapping.confirmed !== false) continue;
    // Граница топографического заказа не заменяет рабочий контур
    // при однозначном назначении обоих контуров
    if (
      workBoundary &&
      layer.id !== workBoundary.id &&
      /топограф/i.test(layer.source_name.split('|')[0] ?? '') &&
      /границ.*заказ/i.test(localLayerName(layer))
    ) {
      next[layer.id] = changeLayerRole(mapping, 'ignore', 'survey_reference');
      continue;
    }
    // При наличии единственного полного контура непригодная граница
    // остаётся замечанием в выборе территории
    if (
      workBoundary &&
      layer.id !== workBoundary.id &&
      isWorkBoundaryName(layer) &&
      layer.boundary_candidate?.status === 'unavailable'
    ) {
      next[layer.id] = {
        ...changeLayerRole(mapping, 'ignore', null),
        category: null,
      };
      continue;
    }
    if (!proposal?.category) continue;
    const nativeReference = nativeReferenceOnly(layer, proposal);
    if (proposal.category === 'cad_reference' && !nativeReference) continue;
    // До подтверждения сноса объект остаётся физическим ограничением
    if (
      proposal.category === 'demolition_object' &&
      proposal.confidence !== 'low'
    ) {
      next[layer.id] = changeLayerRole(
        mapping,
        'restricted',
        proposal.category,
      );
      continue;
    }
    const category = categoryById.get(proposal.category);
    if (
      proposal.category === 'surface_boundary' &&
      category?.kind === null &&
      proposal.confidence === 'high' &&
      mapping.kind === 'ignore'
    ) {
      if (mapping.category !== proposal.category)
        next[layer.id] = { ...mapping, category: proposal.category };
      continue;
    }
    if (!category?.kind || category.kind === 'site_border') continue;
    if (
      proposal.category === 'annotation' &&
      !annotationOnly(layer.entity_types)
    )
      continue;
    // Смешанная физическая геометрия не становится аннотацией
    // даже при пропущенной серверной проверке в старом результате
    if (
      category.kind === 'ignore' &&
      layer.suggested_kind !== 'ignore' &&
      !nativeReference &&
      !(
        proposal.category === 'annotation' && annotationOnly(layer.entity_types)
      )
    )
      continue;
    // Средняя уверенность допускает неизвестные детали физического класса
    // Низкая допускается только для явно распознанной сети без типа
    if (
      proposal.confidence === 'medium' &&
      category.kind === 'ignore' &&
      !(
        proposal.category === 'annotation' && annotationOnly(layer.entity_types)
      )
    )
      continue;
    if (
      proposal.confidence === 'low' &&
      !(
        category.kind === 'utility' &&
        category.category === 'unspecified_network'
      )
    )
      continue;
    next[layer.id] = changeLayerRole(mapping, category.kind, category.category);
  }
  if (
    workBoundary &&
    (!completed || completed.has(workBoundary.id)) &&
    next[workBoundary.id]?.confirmed === false &&
    !Object.values(mappings).some((item) => item.kind === 'site_border')
  )
    next = selectPlanningBoundary(next, workBoundary.id);
  return next;
}
