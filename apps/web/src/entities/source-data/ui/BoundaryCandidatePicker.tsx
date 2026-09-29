import type { Layer, LayerMapping } from '@green/api-client';
import { Text } from '@green/ui';
import type { FC } from 'react';
import { selectPlanningBoundary } from '../model/layerKinds';
import {
  boundaryMeaning, isWorkBoundaryName, localLayerName,
  recommendedWorkBoundary,
} from '../model/boundaryDecision';

interface BoundaryCandidatePickerProps {
  layers: Layer[];
  mappings: Record<string, LayerMapping>;
  onChange: (next: Record<string, LayerMapping>) => void;
  readOnly?: boolean;
  requireAttestation?: boolean;
}

const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

export const BoundaryCandidatePicker: FC<BoundaryCandidatePickerProps> = ({
  layers,
  mappings,
  onChange,
  readOnly = false,
  requireAttestation = true,
}) => {
  const candidates = layers
    .filter((layer) => layer.boundary_candidate?.status === 'usable')
    .sort((left, right) => {
      const priority = (layer: Layer) =>
        isWorkBoundaryName(layer)
          ? 0
          : /границ.*заказ/i.test(localLayerName(layer))
            ? 1
            : 2;
      return (
        priority(left) - priority(right) ||
        localLayerName(left).localeCompare(
          localLayerName(right),
          'ru',
        )
      );
    });
  const recommended = recommendedWorkBoundary(layers);
  const unusableWorkContours = recommended
    ? layers.filter((layer) =>
      layer.id !== recommended.id &&
      isWorkBoundaryName(layer) &&
      layer.boundary_candidate?.status === 'unavailable',
    )
    : [];
  const selected = layers.find(
    (layer) => mappings[layer.id]?.kind === 'site_border',
  );
  if (!candidates.length && !(requireAttestation && selected?.boundary_candidate)) return null;
  return (
    <section className="max-w-180 min-w-0">
      <Text
        as="h3"
        variant="body"
        className="mb-1 font-semibold text-neutral-950"
      >
        Какая территория относится к проекту?
      </Text>
      <Text as="p" variant="caption" className="mb-4 text-neutral-600">
        Топография показывает существующую съёмку, но не имеет приоритета над
        проектной границей. Здесь задаётся предел расчёта; места посадки вы
        выберете позже на карте.
      </Text>
      {unusableWorkContours.length > 0 && (
        <p className="mb-4 text-sm text-neutral-600">
          Другие контуры работ не выбраны: {unusableWorkContours.map((layer) =>
            `${localLayerName(layer)} (${layer.boundary_candidate?.issue ?? 'нет замкнутой площади'})`,
          ).join('; ')}
        </p>
      )}
      {requireAttestation && selected?.boundary_candidate && selected.boundary_candidate.status !== 'usable' && (
        <p role="alert" className="mb-4 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-900">
          Слой «{localLayerName(selected)}» не образует пригодную площадь
          {selected.boundary_candidate?.issue
            ? `: ${selected.boundary_candidate.issue}`
            : ''}
          {candidates.length
            ? '. Выберите замкнутый контур ниже'
            : '. Границу можно не использовать и задать рабочую область на карте'}
        </p>
      )}
      {requireAttestation && selected?.boundary_candidate && selected.boundary_candidate.status !== 'usable' && !candidates.length && !readOnly && (
        <button
          type="button"
          className="mb-4 cursor-pointer text-sm font-medium text-blue-700 underline underline-offset-2"
          onClick={() => onChange(selectPlanningBoundary(mappings, ''))}
        >
          Не использовать эту границу
        </button>
      )}
      <div role="group" aria-label="Граница территории" className="grid gap-2">
        {candidates.map((layer) => {
          const active = selected?.id === layer.id;
          const isRecommended = recommended?.id === layer.id;
          return (
            <button
              key={layer.id}
              type="button"
              aria-pressed={active}
              disabled={readOnly}
              onClick={() =>
                onChange(selectPlanningBoundary(mappings, layer.id))
              }
              className={`flex w-full cursor-pointer items-start gap-3 rounded-lg border border-solid px-4 py-3 text-left disabled:cursor-default ${active ? 'border-blue-600 bg-blue-50' : 'border-neutral-200 bg-white hover:border-blue-300'}`}
            >
              <span
                aria-hidden="true"
                className={`mt-1 size-4 shrink-0 rounded-full border-2 border-solid ${active ? 'border-blue-600 bg-blue-600' : 'border-neutral-400 bg-white'}`}
              />
              <span className="grid min-w-0 gap-1">
                <span className="text-sm font-semibold text-neutral-950">
                  {localLayerName(layer)}
                  {isRecommended && !active && (
                    <span className="ml-2 text-xs font-medium text-blue-700">Рекомендуем</span>
                  )}
                </span>
                <span className="text-sm text-neutral-600">
                  {boundaryMeaning(layer)}
                </span>
                <span className="text-sm text-neutral-600 tabular-nums">
                  {number.format(layer.boundary_candidate?.area_m2 ?? 0)} м²
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
};
