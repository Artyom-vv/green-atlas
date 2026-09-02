export function sourceBlockCaption(blockName: unknown, attributes: unknown): string {
  const name = String(blockName ?? '').trim();
  if (!attributes || typeof attributes !== 'object' || Array.isArray(attributes)) return name;
  const first = Object.entries(attributes as Record<string, unknown>)
    .find(([tag, value]) => tag.trim() && String(value ?? '').trim());
  if (!first) return name;
  const [tag, value] = first;
  const detail = `${tag}: ${String(value).trim()}`.slice(0, 48);
  return name ? `${name}\n${detail}` : detail;
}

export function isPrimarySingleBlockComponent(component: unknown, instances: unknown): boolean {
  return Number(component) === 1 && Number(instances ?? 1) === 1;
}

const SOURCE_LAYER_LABELS: Record<string, string> = {
  OSM_ROAD_LOCAL: 'Местная дорога',
  OSM_ROAD_MAJOR: 'Магистраль',
  OSM_PATH: 'Пешеходная дорожка',
  OSM_GREEN_EXISTING: 'Существующее озеленение',
  OSM_BUILDING: 'Здание',
  SITE_BORDER: 'Граница территории',
};

export function sourceLayerLabel(value: unknown): string {
  const source = String(value ?? '').trim();
  if (!source) return 'Линия DXF';
  return SOURCE_LAYER_LABELS[source.toUpperCase()] ?? source;
}
