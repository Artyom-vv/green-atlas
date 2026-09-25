import type { PatternPreview } from '@green/api-client';
import { Button, Disclosure } from '@green/ui';
import { useState, type FC } from 'react';
import { placementReason } from '../model/patternResultPresentation';
import type { CandidateInspection } from '../model/useCandidateInspection';
import { candidateExamples } from '../model/candidateExamples';

const PAGE_SIZE = 8;
const format = (value: number) =>
  value.toLocaleString('ru-RU', { maximumFractionDigits: 3 });
const shortReason = (
  candidate: NonNullable<PatternPreview['skipped']>[number],
) =>
  candidate.code === 'NATIVE_CLEARANCE'
    ? 'Недостаточный отступ'
    : candidate.code === 'NATIVE_OCCUPIED'
      ? 'Внутри препятствия'
      : candidate.code === 'NATIVE_LOCAL_UNKNOWN'
        ? 'Требуется уточнение'
        : candidate.category === 'spacing'
          ? 'Близко к другой посадке'
          : 'Не пройдена проверка';

export const PlacementCandidateDetails: FC<{
  skipped: NonNullable<PatternPreview['skipped']>;
  inspection?: CandidateInspection;
  spacing?: number;
}> = ({ skipped, inspection, spacing = 0 }) => {
  const examples = candidateExamples(skipped, spacing);
  const [page, setPage] = useState(0);
  const [listOpen, setListOpen] = useState(false);
  const [selection, setSelection] = useState<number>();
  const index = inspection ? inspection.selected?.index : selection;
  const original = index == null ? undefined : skipped[index];
  const trialResult = inspection?.trial?.candidate_results?.at(-1);
  const candidate =
    original && (trialResult ? { ...original, ...trialResult } : original);
  const evidenceCurrent = !inspection?.checking && !inspection?.error;
  if (!examples.length) return null;
  return (
    <section className="grid gap-3" aria-label="Разбор позиций">
      <Disclosure
        title={`Примеры ограничений на карте (${examples.length})`}
        open={listOpen}
        onOpenChange={setListOpen}
      >
        <div className="grid gap-2" aria-label="Непринятые позиции">
          {examples
            .slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
            .map(({ candidate: item, index: number }) => {
              return (
                <Button
                  key={number}
                  variant="secondary"
                  aria-pressed={index === number}
                  onClick={() => {
                    setSelection(number);
                    inspection?.select(item, number);
                    setListOpen(false);
                  }}
                >
                  {number + 1} — {shortReason(item)}
                </Button>
              );
            })}
          <div className="flex items-center justify-between gap-2">
            <Button
              variant="secondary"
              disabled={!page}
              onClick={() => setPage(page - 1)}
            >
              Назад
            </Button>
            <span className="text-xs tabular-nums">
              {page + 1} / {Math.ceil(examples.length / PAGE_SIZE)}
            </span>
            <Button
              variant="secondary"
              disabled={(page + 1) * PAGE_SIZE >= examples.length}
              onClick={() => setPage(page + 1)}
            >
              Далее
            </Button>
          </div>
        </div>
      </Disclosure>
      {candidate && (
        <section
          className="grid gap-3 rounded border border-neutral-300 p-3 text-xs"
          aria-label="Выбранная позиция"
        >
          <h4 className="m-0 text-sm font-semibold">
            Позиция {(index ?? 0) + 1}
          </h4>
          <p className="m-0 leading-4" role="status">
            {inspection?.checking
              ? 'Проверяем новую точку'
              : (inspection?.error ??
                (inspection?.trial?.can_apply
                  ? 'Вариант можно добавить'
                  : placementReason(
                      candidate.code ?? '',
                      candidate.reason,
                    ).split(', слой «')[0]))}
          </p>
          <dl className="m-0 grid gap-2">
            <div>
              <dt className="text-neutral-600">Координаты</dt>
              <dd className="m-0 tabular-nums">
                X {format(inspection?.marker?.coordinate[0] ?? original!.x)}; Y{' '}
                {format(inspection?.marker?.coordinate[1] ?? original!.y)}
              </dd>
            </div>
            {evidenceCurrent &&
              !['NATIVE_OCCUPIED', 'NATIVE_OUTSIDE_SITE'].includes(
                candidate.code ?? '',
              ) &&
              candidate.actual_distance_m != null && (
                <div>
                  <dt className="text-neutral-600">Измеренный отступ</dt>
                  <dd className="m-0">
                    {format(candidate.actual_distance_m)} м
                  </dd>
                </div>
              )}
            {evidenceCurrent &&
              !['NATIVE_OCCUPIED', 'NATIVE_OUTSIDE_SITE'].includes(
                candidate.code ?? '',
              ) &&
              candidate.required_distance_m != null && (
                <div>
                  <dt className="text-neutral-600">Требуемый отступ</dt>
                  <dd className="m-0">
                    {format(candidate.required_distance_m)} м
                  </dd>
                </div>
              )}
          </dl>
          {evidenceCurrent &&
            (candidate.source_layer ||
              candidate.source_feature_ids?.length) && (
              <Disclosure title="Объект исходника">
                {candidate.source_layer && (
                  <p className="m-0 wrap-anywhere">{candidate.source_layer}</p>
                )}
                {!!candidate.source_feature_ids?.length && (
                  <p className="m-0 wrap-anywhere text-neutral-500">
                    {candidate.source_feature_ids.join(', ')}
                  </p>
                )}
              </Disclosure>
            )}
          {inspection && (
            <Button
              variant="secondary"
              onClick={() => inspection.select(original!, index!)}
            >
              Показать исходную точку
            </Button>
          )}
          {inspection && original?.candidate && (
            <>
              <Button
                variant="secondary"
                disabled={inspection.checking}
                onClick={
                  inspection.picking
                    ? inspection.cancelPicking
                    : inspection.startPicking
                }
              >
                {inspection.picking
                  ? 'Отменить выбор точки'
                  : 'Проверить другую точку'}
              </Button>
              {inspection.picking && (
                <span role="status">Выберите точку на карте</span>
              )}
              {inspection.trial?.can_apply && !inspection.error && (
                <Button
                  variant="primary"
                  disabled={inspection.checking || inspection.picking}
                  onClick={inspection.apply}
                >
                  Добавить посадки ({inspection.trial.additions?.length ?? 0})
                </Button>
              )}
            </>
          )}
        </section>
      )}
    </section>
  );
};
