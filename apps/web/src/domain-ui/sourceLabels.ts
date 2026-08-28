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
