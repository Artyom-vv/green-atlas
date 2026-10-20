/** Размещение: прогресс подготовки области по выполненным объектам или площади */
import type { PatternPreview } from '@green/api-client';
import { Progress } from '@green/ui';

type DomainProgressPreview = Pick<PatternPreview, 'search_domains'>;

export function SearchDomainProgress({
  preview,
  running,
}: {
  preview?: DomainProgressPreview;
  running: boolean;
  phase?: 'checking';
}) {
  const domains = preview?.search_domains ?? [];
  const completed = domains.reduce(
    (sum, domain) =>
      sum +
      domain.available_area_m2 +
      domain.excluded_area_m2 +
      domain.unresolved_area_m2,
    0,
  );
  const total =
    completed +
    domains.reduce((sum, domain) => sum + (domain.pending_area_m2 ?? 0), 0);
  const hybrid =
    domains.length > 0 && domains.every((domain) => domain.method === 'hybrid');
  const processedObjects = domains.reduce(
    (sum, domain) => sum + (domain.processed_objects ?? 0),
    0,
  );
  const totalObjects = domains.reduce(
    (sum, domain) => sum + (domain.total_objects ?? 0),
    0,
  );
  const finished =
    domains.length > 0 &&
    domains.every((domain) => domain.stop_reason === 'resolution');
  // До ответа нет измеренного процента. Непустая очередь не округляется до 100.
  const percentage = finished
    ? 100
    : hybrid && totalObjects > 0
      ? Math.min(99, Math.floor((processedObjects / totalObjects) * 100))
      : total > 0
        ? Math.min(99, Math.floor((completed / total) * 100))
        : undefined;
  const valueLabel =
    hybrid && totalObjects > 0
      ? `${processedObjects.toLocaleString('ru-RU')} из ${totalObjects.toLocaleString('ru-RU')}`
      : total > 0
        ? `${completed.toLocaleString('ru-RU', { maximumFractionDigits: 1 })} из ${total.toLocaleString('ru-RU', { maximumFractionDigits: 1 })} м²`
        : undefined;
  return (
    <section aria-label="Ход проверки области">
      <Progress
        label={
          !running
            ? 'Проверка области приостановлена'
            : domains.length === 0
              ? 'Подготавливаем геометрию и ограничения'
              : hybrid
                ? 'Готовим область поиска'
                : 'Проверяем область'
        }
        value={running || domains.length > 0 ? percentage : 0}
        valueLabel={valueLabel}
      />
    </section>
  );
}
