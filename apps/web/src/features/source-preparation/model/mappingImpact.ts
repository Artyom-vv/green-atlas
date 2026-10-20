import type { LayerMapping } from '@green/api-client';
import { canonicalMappingValue } from './canonicalMappingValue';

const key = (
  mappings: LayerMapping[],
  fields: (mapping: LayerMapping) => unknown[],
) =>
  JSON.stringify(
    mappings
      .map((mapping) => [mapping.layer_id, ...fields(mapping)])
      .sort(([left], [right]) => String(left).localeCompare(String(right)))
      .map(canonicalMappingValue),
  );

/** These fields change the immutable physical projection on the backend.
 * Network meaning and evidence rebind locally; visibility only changes display. */
export function mappingImpact(saved: LayerMapping[], draft: LayerMapping[]) {
  const physical = (mapping: LayerMapping) => [
    mapping.kind,
    mapping.category ?? null,
    mapping.confirmed === undefined ? true : mapping.confirmed,
    mapping.utility_axis_bindings ?? [],
  ];
  const metadata = (mapping: LayerMapping) => [mapping.utility_context ?? null];
  const display = (mapping: LayerMapping) => [mapping.visible ?? true];
  return {
    physicalChanged: key(saved, physical) !== key(draft, physical),
    metadataChanged: key(saved, metadata) !== key(draft, metadata),
    displayChanged: key(saved, display) !== key(draft, display),
  };
}
