import { useId } from 'react';
import type { PlacementMaskPreset } from '@green/api-client';

export type PlacementScenarioId = 'natural' | PlacementMaskPreset['id'];

const SCENARIOS: ReadonlyArray<{
  id: PlacementScenarioId;
  title: string;
  description: string;
}> = [
  {
    id: 'natural',
    title: 'Свободно',
    description: 'Естественно заполняет подходящие места без жёсткого ритма',
  },
  {
    id: 'regular_grid',
    title: 'Регулярная сетка',
    description: 'Чёткие ряды с единым шагом для дворов и парадных зон',
  },
  {
    id: 'road_edges',
    title: 'Вдоль дорожек',
    description: 'Выстраивает посадки по найденным маршрутам внутри участка',
  },
  {
    id: 'cluster_groves',
    title: 'Куртины',
    description: 'Собирает компактные группы в нескольких подходящих местах',
  },
];

export function PlacementScenarioPicker({ value, presets, disabled = false, onChange }: {
  value: PlacementScenarioId;
  presets?: PlacementMaskPreset[];
  disabled?: boolean;
  onChange: (value: PlacementScenarioId) => void;
}) {
  const descriptionPrefix = useId();
  const presetsById = new Map(presets?.map((preset) => [preset.id, preset]));

  return <fieldset className="placement-scenario-picker">
    <legend>
      <span>Сценарий размещения</span>
      <small>Маска задаёт логику расстановки</small>
    </legend>
    <div className="placement-scenario-picker__grid">
      {SCENARIOS.map((scenario) => {
        const preset = scenario.id === 'natural' ? undefined : presetsById.get(scenario.id);
        const unavailable = preset?.available === false;
        const description = unavailable
          ? preset.unavailable_reason ?? 'Сценарий недоступен для этого чертежа'
          : preset?.description ?? scenario.description;
        const descriptionId = `${descriptionPrefix}-${scenario.id}`;
        return <label key={scenario.id} className="placement-scenario-card" data-selected={value === scenario.id || undefined} data-unavailable={unavailable || undefined}>
          <input
            type="radio"
            name="placement-scenario"
            value={scenario.id}
            checked={value === scenario.id}
            disabled={disabled || unavailable}
            aria-label={preset?.title ?? scenario.title}
            aria-describedby={descriptionId}
            onChange={() => { if (!disabled && !unavailable) onChange(scenario.id); }}
          />
          <span className={`placement-scenario-card__diagram placement-scenario-card__diagram--${scenario.id}`} aria-hidden="true">
            <i /><i /><i /><i /><i /><i />
          </span>
          <span className="placement-scenario-card__copy">
            <strong>{preset?.title ?? scenario.title}</strong>
            <small id={descriptionId}>{description}</small>
          </span>
          <span className="placement-scenario-card__radio" aria-hidden="true" />
        </label>;
      })}
    </div>
    <p className="placement-scenario-picker__note">Сценарий только предлагает места. Отступы и ограничения проверяются для каждой позиции.</p>
  </fieldset>;
}
