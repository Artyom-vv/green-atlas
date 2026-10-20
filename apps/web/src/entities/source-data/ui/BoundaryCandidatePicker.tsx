import type { Layer, LayerMapping } from '@green/api-client';
import {
  Button,
  Disclosure,
  IconButton,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
  Radio,
} from '@green/ui';
import { Info } from 'lucide-react';
import { useId, type FC } from 'react';
import { selectPlanningBoundary } from '../model/layerKinds';
import {
  boundaryMeaning,
  isWorkBoundaryName,
  localLayerName,
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

/** Boundary help can share the task heading without adding a content row. */
export function BoundaryCandidateInformation({
  layers,
  className,
}: {
  layers: Layer[];
  className?: string;
}) {
  const candidates = layers.filter(
    (layer) => layer.boundary_candidate?.status === 'usable',
  );
  return (
    <Popover>
      <PopoverTrigger
        render={
          <IconButton
            variant="ghost"
            icon={Info}
            label="О выборе границы"
            className={className}
          />
        }
      />
      <PopoverPopup align="end" className="w-80" aria-label="Выбор границы">
        <PopoverTitle className="m-0 text-sm font-medium">
          Граница расчёта
        </PopoverTitle>
        <p className="mt-3 mb-0 text-neutral-600">
          Граница задаёт предел расчёта. Места посадки выбираются на карте
        </p>
        <dl className="m-0 mt-3 grid gap-2">
          {candidates.map((layer) => (
            <div key={layer.id}>
              <dt className="wrap-anywhere">{localLayerName(layer)}</dt>
              <dd className="m-0 text-neutral-600">{boundaryMeaning(layer)}</dd>
            </div>
          ))}
        </dl>
      </PopoverPopup>
    </Popover>
  );
}

export const BoundaryCandidatePicker: FC<BoundaryCandidatePickerProps> = ({
  layers,
  mappings,
  onChange,
  readOnly = false,
  requireAttestation = true,
}) => {
  const boundaryGroupName = useId();
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
        localLayerName(left).localeCompare(localLayerName(right), 'ru')
      );
    });
  const recommended = recommendedWorkBoundary(layers);
  const unusableWorkContours = recommended
    ? layers.filter(
        (layer) =>
          layer.id !== recommended.id &&
          isWorkBoundaryName(layer) &&
          layer.boundary_candidate?.status === 'unavailable',
      )
    : [];
  const selected = layers.find(
    (layer) => mappings[layer.id]?.kind === 'site_border',
  );
  if (
    !candidates.length &&
    !(requireAttestation && selected?.boundary_candidate)
  )
    return null;
  return (
    <section className="min-w-0 text-sm leading-5">
      {requireAttestation &&
        selected?.boundary_candidate &&
        selected.boundary_candidate.status !== 'usable' && (
          <p
            role="alert"
            className="text-warning-strong m-0 px-6 py-3 max-[719px]:px-4"
          >
            Слой «{localLayerName(selected)}» не образует пригодную площадь
            {selected.boundary_candidate?.issue
              ? `: ${selected.boundary_candidate.issue}`
              : ''}
            {candidates.length
              ? '. Выберите замкнутый контур'
              : '. Рабочую область можно задать на карте'}
          </p>
        )}
      {requireAttestation &&
        selected?.boundary_candidate &&
        selected.boundary_candidate.status !== 'usable' &&
        !candidates.length &&
        !readOnly && (
          <div className="px-6 pb-3 max-[719px]:px-4">
            <Button
              variant="ghost"
              className="-ml-3 text-blue-600"
              onClick={() => onChange(selectPlanningBoundary(mappings, ''))}
            >
              Не использовать эту границу
            </Button>
          </div>
        )}
      <div role="group" aria-label="Граница территории">
        {candidates.map((layer) => {
          const active = selected?.id === layer.id;
          const isRecommended = recommended?.id === layer.id;
          return (
            <Radio
              key={layer.id}
              name={boundaryGroupName}
              aria-label={localLayerName(layer)}
              controlSize="default"
              checked={active}
              disabled={readOnly}
              onChange={(event) => {
                if (event.target.checked)
                  onChange(selectPlanningBoundary(mappings, layer.id));
              }}
              className="min-h-14 w-full gap-4 border-0 border-t border-solid border-neutral-200 bg-white px-6 py-3 text-left hover:bg-neutral-100 has-[:checked]:bg-blue-100 max-[719px]:px-4 [&>span:last-child]:flex-1"
              label={
                <span className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-4">
                  <span className="grid min-w-0">
                    <span className="wrap-anywhere text-neutral-800">
                      {localLayerName(layer)}
                    </span>
                    {isRecommended && (
                      <span className="text-xs leading-4 text-blue-600">
                        Рекомендуем
                      </span>
                    )}
                  </span>
                  <span className="text-neutral-600 tabular-nums">
                    {number.format(layer.boundary_candidate?.area_m2 ?? 0)} м²
                  </span>
                </span>
              }
            />
          );
        })}
      </div>
      {unusableWorkContours.length > 0 && (
        <Disclosure
          variant="plain"
          title={`Другие контуры работ (${unusableWorkContours.length})`}
          className="px-6 pb-3 max-[719px]:px-4"
        >
          <dl className="m-0 grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-x-4 gap-y-2 max-[719px]:grid-cols-1">
            {unusableWorkContours.map((layer) => (
              <div key={layer.id} className="contents">
                <dt className="wrap-anywhere">{localLayerName(layer)}</dt>
                <dd className="m-0 wrap-anywhere text-neutral-600">
                  {layer.boundary_candidate?.issue ?? 'Нет замкнутой площади'}
                </dd>
              </div>
            ))}
          </dl>
        </Disclosure>
      )}
    </section>
  );
};
