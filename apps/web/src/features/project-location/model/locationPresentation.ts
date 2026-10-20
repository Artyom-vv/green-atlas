import type { LocationSuggestion } from '@green/api-client';

const normalized = (value: string) => value.toLocaleLowerCase('ru-RU');
function matchesName(part: string, name?: string | null) {
  if (!name) return false;
  const text = normalized(part),
    value = normalized(name);
  return (
    text === value || text.endsWith(` ${value}`) || text.startsWith(`${value} `)
  );
}
function addressParts(item: LocationSuggestion) {
  return item.label.split(',').map((value) => value.trim());
}
export function locationLabel(item?: LocationSuggestion) {
  if (!item) return '';
  const part = addressParts(item).find((value) =>
    matchesName(value, item.street),
  );
  const street =
    part?.replace(/^ул\s+/i, '').replace(/\s+пр-д$/i, ' проезд') ?? item.street;
  return [item.city, street].filter(Boolean).join(', ') || item.label;
}
export function locationDescription(item: LocationSuggestion) {
  return addressParts(item)
    .filter(
      (value) =>
        !/^\d{6}$/.test(value) &&
        !matchesName(value, item.city) &&
        !matchesName(value, item.street),
    )
    .join(', ');
}
export const locationMetric = (value?: number | null) =>
  value == null
    ? 'Нет данных'
    : `${value.toLocaleString('ru-RU', { maximumFractionDigits: 2 })} м`;
