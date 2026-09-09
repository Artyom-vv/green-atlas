export function allocationHint(total: number, count: number): string {
  if (!count) return '';
  const base = Math.floor(total / count), extra = total % count;
  if (!extra) return `По ${base} на каждый участок`;
  if (extra === 1) return `Первый участок: ${base + 1}. Остальные: по ${base}.`;
  return `По ${base + 1} на первые ${extra}. На остальные: по ${base}.`;
}
