import type { PatternPreview } from '@green/api-client';
import { isGeneralDataNotice } from '@/entities/validation/model/issueGroups';

// Коды сохраняют смысл старых ответов; адреса и расстояния берутся из свидетельств.
const REASON_LABELS: Record<string, string> = {
  NATIVE_OCCUPIED: 'Внутри запрещённой области',
  NATIVE_CONSTRAINT: 'Не пройдена проверка ограничений',
  NATIVE_OUTSIDE_SITE: 'За границей территории',
  NATIVE_LOCAL_UNKNOWN: 'Геометрия или назначение объектов не подтверждены',
  SOURCE_GEOMETRY_PARTIAL: 'Недостаточно исходных данных',
  SAFE_CAPACITY_REACHED: 'Недостаточно предложенных позиций',
};

export function placementReason(code: string, message: string): string {
  // Современный ответ объясняет конкретное препятствие; переводим только старый протокол.
  return /native_|AutoCAD API:/.test(message)
    ? (REASON_LABELS[code] ?? 'Не пройдена проверка позиции')
    : message;
}

export function patternResultHeading(preview: PatternPreview): string {
  if (preview.accepted_count)
    return `Найдено ${preview.accepted_count} из ${preview.requested_count}`;
  if (
    preview.search_domains?.some(
      (domain) =>
        domain.unresolved_area_m2 > 0 || (domain.pending_area_m2 ?? 0) > 0,
    ) ||
    preview.reason_summary?.some(
      (reason) =>
        reason.status === 'unknown' && !isGeneralDataNotice(reason.code),
    ) ||
    preview.skipped?.some(
      (candidate) =>
        candidate.status === 'unknown' && !isGeneralDataNotice(candidate.code),
    )
  )
    return 'Не удалось подтвердить места';
  if (preview.generated_count === 0) return 'Способ не сформировал позиции';
  return 'Предложенные позиции не приняты';
}

/** Объяснение завершённого поиска не подменяет проверку полной вместимости. */
export function patternSearchNotice(
  preview: PatternPreview,
): string | undefined {
  if (
    preview.accepted_count >= preview.requested_count ||
    preview.search_domains?.some(
      (domain) => domain.stop_reason !== 'resolution',
    )
  )
    return undefined;
  switch (preview.search_stop_reason) {
    case 'candidate_limit':
      return 'Ограниченный поиск завершён. Другие допустимые места могут остаться.';
    case 'domain_exhausted':
      return 'Способ не предложил больше позиций. Это не оценка полной вместимости участка.';
    case 'time_limit':
      return 'Поиск остановлен по лимиту времени. Другие допустимые места могут остаться.';
    default:
      return undefined;
  }
}

export function patternResultReasons(preview: PatternPreview) {
  const groups = new Map<string, { count: number; message: string }>();
  for (const reason of preview.reason_summary ?? []) {
    if (reason.status !== 'blocked' && isGeneralDataNotice(reason.code))
      continue;
    // Один итог по расстояниям сохраняет причину отказа у существующих посадок,
    // не превращая технические повторы около новых посадок в журнал проб.
    if (reason.category === 'spacing' && reason.status === 'blocked') {
      const key = 'blocked:spacing';
      const existing = groups.get(key);
      if (existing) existing.count += reason.count;
      else
        groups.set(key, {
          count: reason.count,
          message: 'Недостаточно расстояния до других посадок',
        });
      continue;
    }
    const message =
      reason.code === 'NATIVE_CLEARANCE'
        ? 'Недостаточный отступ от препятствий'
        : reason.code === 'NATIVE_OCCUPIED'
          ? 'Внутри запрещённой области'
          : placementReason(reason.code, reason.message).split(', слой «')[0];
    const key = `${reason.status}:${reason.code}:${message}`;
    const existing = groups.get(key);
    if (existing) existing.count += reason.count;
    else groups.set(key, { count: reason.count, message });
  }
  return [...groups.entries()].map(([key, reason]) => ({ key, ...reason }));
}
