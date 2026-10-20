/** Рабочая область: завершение подготовки областей без повторного действия пользователя */
import type { PatternPreview } from '@green/api-client';
import {
  PATTERN_PASS_RESPONSE_TIMEOUT_MS,
  patternPreviewPass,
} from './patternPreviewPass';

const completedWork = (
  domain: NonNullable<PatternPreview['search_domains']>[number],
) =>
  domain.method === 'hybrid' ? domain.processed_objects : domain.measured_cells;

type DomainPreview = Pick<PatternPreview, 'search_domains' | 'change_set'>;

/** Продолжение подготовки областей для всех вариантов размещения. Неопределённая
 * площадь остаётся результатом проверки, незавершённая очередь требует продолжения */
export async function completeSearchDomainPreview<
  Preview extends DomainPreview,
>(
  next: (signal: AbortSignal) => Promise<Preview>,
  signal: AbortSignal,
  publish?: (progress: Preview) => void,
  passTimeoutMs = PATTERN_PASS_RESPONSE_TIMEOUT_MS,
): Promise<Preview> {
  let previous: Preview | undefined;
  while (true) {
    signal.throwIfAborted();
    const result = await patternPreviewPass(next, signal, passTimeoutMs);
    signal.throwIfAborted();
    const unfinished = result.search_domains?.some(
      (domain) => domain.stop_reason !== 'resolution',
    );
    if (!unfinished) return result;
    // Промежуточный вариант не становится применимой записью плана. Старый
    // сервер может вернуть кандидатов вместе с незавершённой областью.
    publish?.({
      ...result,
      change_set: undefined,
      ...('accepted_count' in result ? { accepted_count: 0 } : {}),
      ...('found' in result ? { found: 0 } : {}),
    });
    if (previous) {
      const prior = new Map(
        previous.search_domains?.map((domain, index) => [
          `${domain.zone_id}:${index}`,
          domain,
        ]),
      );
      // Автоподбор возвращает варианты в устойчивом порядке: дерево и кустарник
      // могут проверять один участок с одинаковой ревизией общей геометрии.
      const advanced = result.search_domains?.some((domain, index) => {
        const before = prior.get(`${domain.zone_id}:${index}`);
        return !before || completedWork(domain) > completedWork(before);
      });
      const regressed = result.search_domains?.some((domain, index) => {
        const before = prior.get(`${domain.zone_id}:${index}`);
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
