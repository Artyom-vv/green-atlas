import type { SceneSnapshot } from '../contracts';
import type { WireSchema } from '../contracts/wire';

export function normalizeScene(
  source: WireSchema<'SceneSnapshot'>,
): SceneSnapshot {
  return {
    ...source,
    objects: [...(source.objects ?? [])],
    data_gaps: [...(source.data_gaps ?? [])],
  };
}
