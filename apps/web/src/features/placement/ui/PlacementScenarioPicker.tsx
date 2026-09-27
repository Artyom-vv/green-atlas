import { useId, type FC } from 'react';
import { Grid2X2, Route, Trees, Waypoints } from 'lucide-react';
import { FieldGrid, cx } from '@green/ui';
import type { PlacementMaskPreset } from '@green/api-client';
import type { PlacementScenarioId } from '../model/patternForm';

export type { PlacementScenarioId } from '../model/patternForm';

const SCENARIOS: ReadonlyArray<{
  id: PlacementScenarioId;
  title: string;
  description: string;
  icon: typeof Trees;
}> = [
  {
    id: 'natural',
    icon: Waypoints,
    title: 'Без рядов',
    description: 'Нерегулярное размещение внутри участка',
  },
  {
    id: 'regular_grid',
    icon: Grid2X2,
    title: 'Регулярная сетка',
    description: 'Чёткие ряды с единым шагом для дворов и парадных зон',
  },
  {
    id: 'road_edges',
    icon: Route,
    title: 'Вдоль дорожек',
    description: 'Выстраивает посадки по найденным маршрутам внутри участка',
  },
  {
    id: 'cluster_groves',
    icon: Trees,
    title: 'Куртины',
    description: 'Собирает компактные группы в нескольких подходящих местах',
  },
];

export interface PlacementScenarioPickerProps {
  value: PlacementScenarioId;
  presets?: PlacementMaskPreset[];
  disabled?: boolean;
  onChange: (value: PlacementScenarioId) => void;
}

export const PlacementScenarioPicker: FC<PlacementScenarioPickerProps> = ({
  value,
  presets,
  disabled = false,
  onChange,
}) => {
  const descriptionPrefix = useId();
  const groupName = `${descriptionPrefix}-placement-scenario`;
  const presetsById = new Map(presets?.map((preset) => [preset.id, preset]));
  return (
    <fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
      <legend className="mb-2 text-xs font-semibold">Размещение</legend>
      <FieldGrid minWidth={140} className="gap-2">
        {SCENARIOS.map((scenario) => {
          const preset =
            scenario.id === 'natural'
              ? undefined
              : presetsById.get(scenario.id);
          const unavailable = preset?.available === false;
          const description = unavailable
            ? (preset.unavailable_reason ?? 'Недоступно для этого чертежа')
            : (preset?.description ?? scenario.description);
          const title = preset?.title ?? scenario.title;
          const Icon = scenario.icon;
          return (
            <label
              key={scenario.id}
              title={description}
              className={cx(
                'flex min-h-8 min-w-0 cursor-pointer items-center gap-2 rounded-(--radius-control) border border-solid px-2 py-1 text-xs leading-4 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-blue-600',
                value === scenario.id
                  ? 'border-blue-600 bg-blue-100 text-blue-700'
                  : 'border-neutral-300 bg-white',
                (disabled || unavailable) && 'cursor-not-allowed opacity-50',
              )}
            >
              <input
                type="radio"
                className="sr-only"
                name={groupName}
                value={scenario.id}
                checked={value === scenario.id}
                disabled={disabled || unavailable}
                aria-label={title}
                aria-describedby={`${descriptionPrefix}-${scenario.id}`}
                onChange={() => {
                  if (!disabled && !unavailable) onChange(scenario.id);
                }}
              />
              <Icon className="shrink-0" size={15} aria-hidden="true" />
              <span>{title}</span>
              <span
                id={`${descriptionPrefix}-${scenario.id}`}
                className="sr-only"
              >
                {description}
              </span>
            </label>
          );
        })}
      </FieldGrid>
      {presets
        ?.filter((preset) => !preset.available)
        .map((preset) => (
          <p key={preset.id} className="m-0 text-xs leading-4 text-neutral-600">
            {preset.title}:{' '}
            {preset.unavailable_reason ?? 'Нет необходимых данных'}
          </p>
        ))}
    </fieldset>
  );
};
