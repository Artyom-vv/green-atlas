import type { Layer } from '@green/api-client';

export const localLayerName = (layer: Layer) =>
  layer.source_name.split(/\||\$\d+\$/).at(-1) ?? layer.source_name;

export const isWorkBoundaryName = (layer: Layer) =>
  /границ.*(?:работ|проектир|благоустр)/i.test(localLayerName(layer));

export function recommendedWorkBoundary(layers: Layer[]): Layer | undefined {
  const candidates = layers.filter((layer) =>
    isWorkBoundaryName(layer) &&
    layer.boundary_candidate?.status === 'usable' &&
    layer.boundary_candidate.component_count === 1 &&
    (layer.boundary_candidate.inset_1_5m_area_m2 ?? 0) > 0 &&
    layer.geometry_complete,
  );
  return candidates.length === 1 ? candidates[0] : undefined;
}

export function boundaryMeaning(layer: Layer): string {
  const name = localLayerName(layer);
  if (/границ.*работ/i.test(name))
    return 'Контур зоны работ. Ограничивает территорию расчёта, не отдельные участки посадки';
  if (/границ.*(?:проектир|благоустр)/i.test(name))
    return 'Проектный контур. Подойдёт только если образует замкнутую область';
  if (/границ.*заказ/i.test(name))
    return 'Общий охват исходной съёмки. Может быть шире зоны проектных работ';
  return 'Контур из чертежа. Проверьте его назначение перед расчётом';
}
