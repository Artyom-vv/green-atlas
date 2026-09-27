import { ApiClientError, type Layer, type LayerMapping } from '@green/api-client';

/** Resolve server field indices against the mapping order sent by this form. */
export function preparationFieldErrors(
  error: unknown,
  layers: Layer[],
  mappings: Record<string, LayerMapping>,
): string[] {
  if (!(error instanceof ApiClientError)) return [];
  const submitted = Object.values(mappings);
  return Object.entries(error.fieldErrors).flatMap(([field, messages]) => {
    const index = /^mappings\.(\d+)(?:\.|$)/.exec(field)?.[1];
    const mapping = index === undefined ? undefined : submitted[Number(index)];
    const layer = layers.find((item) => item.id === mapping?.layer_id);
    return messages.map((message) =>
      `${layer?.source_name ?? field}: ${message.replace(/^Value error, /, '')}`);
  });
}
