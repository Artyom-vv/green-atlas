import type { PatternPreview } from '@green/api-client';

// Stable codes also cover previews saved before human-readable API messages.
// Handles and distances remain in the candidate evidence, never guessed from prose.
const REASON_LABELS: Record<string, string> = {
  NATIVE_OCCUPIED: 'Внутри запрещённой области',
  NATIVE_CONSTRAINT: 'Не пройдена проверка ограничений',
  NATIVE_OUTSIDE_SITE: 'За границей территории',
  NATIVE_LOCAL_UNKNOWN: 'Геометрия или назначение объектов не подтверждены',
  SOURCE_GEOMETRY_PARTIAL: 'Недостаточно исходных данных',
  SAFE_CAPACITY_REACHED: 'Недостаточно предложенных позиций',
};

export function placementReason(code: string, message: string): string {
  // Current API messages explain the actual obstacle. Only translate old
  // protocol prose; never replace a precise cause with a generic diagnosis.
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
    preview.reason_summary?.some((reason) => reason.status === 'unknown') ||
    preview.skipped?.some((candidate) => candidate.status === 'unknown')
  )
    return 'Не удалось подтвердить места';
  if (preview.generated_count === 0) return 'Способ не сформировал позиции';
  return 'Предложенные позиции не приняты';
}

export function patternResultReasons(preview: PatternPreview) {
  const groups = new Map<string, { count: number; message: string }>();
  for (const reason of preview.reason_summary ?? []) {
    if (reason.category === 'spacing') continue;
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
