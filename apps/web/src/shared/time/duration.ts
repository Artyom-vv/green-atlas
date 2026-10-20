/** Общее представление длительности локальных операций без оценки оставшегося времени. */
export const CLOCK_INTERVAL_MS = 1_000;
const SECONDS_PER_MINUTE = 60;

export function formatDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / CLOCK_INTERVAL_MS));
  if (seconds < SECONDS_PER_MINUTE) return `${seconds} с`;
  return `${Math.floor(seconds / SECONDS_PER_MINUTE)} мин ${seconds % SECONDS_PER_MINUTE} с`;
}
