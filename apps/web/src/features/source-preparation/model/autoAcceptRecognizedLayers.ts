import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
import { changeLayerRole, selectPlanningBoundary } from '@/entities/source-data/model/layerKinds';
import {
  isWorkBoundaryName, localLayerName, recommendedWorkBoundary,
} from '@/entities/source-data/model/boundaryDecision';

const annotationEntityTypes = new Set([
  'TEXT', 'MTEXT', 'DIMENSION', 'LEADER', 'MLEADER', 'AUTOCAD:AcDbMLeader',
]);

function isPureReviewMark(layer: Layer): boolean {
  const kinds = Object.keys(layer.entity_types ?? {});
  return /(?:вопрос|замечан|коммент|выноск)/i.test(localLayerName(layer)) &&
    kinds.length > 0 &&
    kinds.every((kind) => kind === 'CIRCLE' || annotationEntityTypes.has(kind));
}

/** Apply only roles supported by source semantics and usable geometry.
 * Descriptive categories do not become physical constraints by default. */
export function autoAcceptRecognizedLayers(
  layers: Layer[],
  mappings: Record<string, LayerMapping>,
  recognition?: LayerRecognition,
): Record<string, LayerMapping> {
  if (recognition?.status !== 'completed') return mappings;
  const categoryById = new Map(
    recognition.categories.map((item) => [item.category, item]),
  );
  const proposalById = new Map(
    recognition.proposals.map((item) => [item.layer_id, item]),
  );
  let next = { ...mappings };
  const workBoundary = recommendedWorkBoundary(layers);
  for (const layer of layers) {
    const mapping = mappings[layer.id];
    const proposal = proposalById.get(layer.id);
    if (!mapping || mapping.confirmed !== false)
      continue;
    // A survey's order extent is not the smaller, usable work contour. This
    // rule requires both source semantics and another unambiguous boundary.
    if (
      workBoundary &&
      layer.id !== workBoundary.id &&
      /топограф/i.test(layer.source_name.split('|')[0] ?? '') &&
      /границ.*заказ/i.test(localLayerName(layer))
    ) {
      next[layer.id] = changeLayerRole(mapping, 'ignore', 'survey_reference');
      continue;
    }
    // A second project/work border with no calculable surface cannot be used
    // as the territory when another single complete contour is available.
    // Its geometry issue remains visible in the territory picker.
    if (
      workBoundary &&
      layer.id !== workBoundary.id &&
      isWorkBoundaryName(layer) &&
      layer.boundary_candidate?.status === 'unavailable'
    ) {
      next[layer.id] = {
        ...changeLayerRole(mapping, 'ignore', null), category: null,
      };
      continue;
    }
    if (!proposal?.category) continue;
    // Until completed demolition is proven, its physical geometry remains a
    // conservative constraint. Do not infer the object has disappeared.
    if (proposal.category === 'demolition_object' && proposal.confidence !== 'low') {
      next[layer.id] = changeLayerRole(mapping, 'restricted', proposal.category);
      continue;
    }
    const category = categoryById.get(proposal.category);
    if (!category?.kind || category.kind === 'site_border') continue;
    // A model's confidence cannot turn a mixed physical line into an
    // annotation. Keep it pending even if an older API/cache omitted the
    // server-side exclusion guard.
    if (
      category.kind === 'ignore' &&
      layer.suggested_kind !== 'ignore' &&
      !(
        proposal.category === 'annotation' &&
        layer.entity_types &&
        Object.keys(layer.entity_types).length > 0 &&
        Object.keys(layer.entity_types).every((kind) => annotationEntityTypes.has(kind))
      )
    ) continue;
    // Medium means the physical class is usable but a detail (e.g. the network
    // subtype or exact equipment) remains unknown. Low is accepted only for a
    // visible line explicitly recognised as an unspecified network.
    if (
      proposal.confidence === 'medium' &&
      category.kind === 'ignore' &&
      !(proposal.category === 'annotation' && isPureReviewMark(layer))
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
    next[workBoundary.id]?.confirmed === false &&
    !Object.values(mappings).some((item) => item.kind === 'site_border')
  ) next = selectPlanningBoundary(next, workBoundary.id);
  return next;
}
