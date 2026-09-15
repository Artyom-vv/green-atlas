const projectDate = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
});
const megabytes = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const BYTES_PER_MEBIBYTE = 1024 ** 2;

export function formatProjectDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '—' : projectDate.format(date);
}

export function formatFileSize(value?: number | null) {
  return value ? `${megabytes.format(value / BYTES_PER_MEBIBYTE)} МБ` : '—';
}
