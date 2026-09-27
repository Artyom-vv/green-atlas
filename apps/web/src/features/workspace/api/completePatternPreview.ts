import type { PatternPreview } from '@green/api-client';
import {
  PATTERN_PASS_RESPONSE_TIMEOUT_MS,
  patternPreviewPass,
} from './patternPreviewPass';

const completedWork = (
  domain: NonNullable<PatternPreview['search_domains']>[number],
) =>
  domain.method === 'hybrid' ? domain.processed_objects : domain.measured_cells;

/** Continue native preparation, not candidate retries. Unknown area is a
 * completed answer; only a nonempty work queue needs another pass. */
export async function completePatternPreview(
  next: (signal: AbortSignal) => Promise<PatternPreview>,
  signal: AbortSignal,
  publish?: (progress: PatternPreview) => void,
  passTimeoutMs = PATTERN_PASS_RESPONSE_TIMEOUT_MS,
): Promise<PatternPreview> {
  let previous: PatternPreview | undefined;
  while (true) {
    signal.throwIfAborted();
    const result = await patternPreviewPass(next, signal, passTimeoutMs);
    signal.throwIfAborted();
    const unfinished = result.search_domains?.some(
      (domain) => domain.stop_reason !== 'resolution',
    );
    if (!unfinished) return result;
    // Old servers may still send candidates for a partial mask. Never expose
    // them as an applicable final proposal while continuation is running.
    publish?.({ ...result, change_set: undefined, accepted_count: 0 });
    if (previous) {
      const prior = new Map(
        previous.search_domains?.map((domain) => [domain.zone_id, domain]),
      );
      const advanced = result.search_domains?.some((domain) => {
        const before = prior.get(domain.zone_id);
        return !before || completedWork(domain) > completedWork(before);
      });
      const regressed = result.search_domains?.some((domain) => {
        const before = prior.get(domain.zone_id);
        return (
          before &&
          (domain.revision !== before.revision ||
            domain.method !== before.method ||
            completedWork(domain) < completedWork(before))
        );
      });
      if (!advanced || regressed)
        throw new Error('Проверка области прервана — повторите расчёт');
    }
    previous = result;
  }
}
