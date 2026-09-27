import type { PatternPreview } from '@green/api-client';
import { useEffect, useState } from 'react';
import { Progress } from '@green/ui';
import { SearchDomainSummary } from './SearchDomainSummary';

const SLOW_BATCH_NOTICE_SECONDS = 30;

export function SearchDomainProgress({
  preview,
  running,
}: {
  preview?: PatternPreview;
  running: boolean;
}) {
  const domains = preview?.search_domains ?? [];
  const [replyClock, setReplyClock] = useState<{
    basis?: PatternPreview;
    seconds: number;
  }>({ seconds: 0 });
  const replyAge = replyClock.basis === preview ? replyClock.seconds : 0;
  useEffect(() => {
    if (!running) return;
    const receivedAt = Date.now();
    const timer = setInterval(
      () =>
        setReplyClock({
          basis: preview,
          seconds: Math.floor((Date.now() - receivedAt) / 1000),
        }),
      1000,
    );
    return () => clearInterval(timer);
  }, [preview, running]);
  const completed = domains.reduce(
    (sum, domain) =>
      sum +
      domain.available_area_m2 +
      domain.excluded_area_m2 +
      domain.unresolved_area_m2,
    0,
  );
  const pending = domains.reduce(
    (sum, domain) => sum + (domain.pending_area_m2 ?? 0),
    0,
  );
  const total = completed + pending;
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
  // Whole-cell subdivision changes the number of remaining cells. Area, not
  // cells/time/pass count, is the fixed denominator. Never round pending to 100%.
  const percentage = finished
    ? 100
    : hybrid && totalObjects > 0
      ? Math.min(99, Math.floor((processedObjects / totalObjects) * 100))
      : total > 0
        ? Math.min(99, Math.floor((completed / total) * 100))
        : 0;
  const measured = domains.reduce(
    (sum, domain) => sum + domain.measured_cells,
    0,
  );
  return (
    <section aria-label="Ход проверки области" className="grid gap-3">
      <h3 className="m-0 text-sm font-semibold">
        {running
          ? hybrid
            ? 'Готовим область поиска'
            : 'Проверяем область'
          : 'Проверка области приостановлена'}
      </h3>
      {total > 0 ? (
        <>
          <dl className="m-0 grid gap-2 text-xs">
            <div className="flex justify-between gap-3">
              <dt>
                {hybrid ? 'Обработано объектов' : 'Выполнено проверок ячеек'}
              </dt>
              <dd className="m-0 font-semibold tabular-nums">
                {hybrid
                  ? `${processedObjects.toLocaleString('ru-RU')} из ${totalObjects.toLocaleString('ru-RU')}`
                  : measured.toLocaleString('ru-RU')}
              </dd>
            </div>
            {running && (
              <div className="flex justify-between gap-3 text-neutral-600">
                <dt>Последнее обновление</dt>
                <dd className="m-0 tabular-nums">{replyAge} с назад</dd>
              </div>
            )}
          </dl>
          <Progress
            label={
              hybrid
                ? 'Подготовка ограничений'
                : 'Площадь с готовым результатом'
            }
            value={percentage}
          />
          {!hybrid && (
            <p className="m-0 text-xs text-neutral-600" role="status">
              {completed.toLocaleString('ru-RU', { maximumFractionDigits: 1 })}{' '}
              из {total.toLocaleString('ru-RU', { maximumFractionDigits: 1 })}{' '}
              м²
            </p>
          )}
          {running && (
            <p className="m-0 text-xs text-neutral-600" role="status">
              {hybrid
                ? 'Строим отступы по геометрии AutoCAD. Посадки пройдут отдельную проверку.'
                : replyAge >= SLOW_BATCH_NOTICE_SECONDS
                  ? 'Ожидаем ответ AutoCAD по текущей партии'
                  : 'Проверяем и уточняем оставшиеся области'}
            </p>
          )}
          <SearchDomainSummary domains={domains} showPendingNotice={false} />
        </>
      ) : (
        <p className="m-0 text-xs text-neutral-600" role="status">
          Подготавливаем геометрию и ограничения
        </p>
      )}
    </section>
  );
}
