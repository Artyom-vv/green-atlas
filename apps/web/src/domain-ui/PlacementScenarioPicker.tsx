import { useId } from 'react';
import { Grid2X2, Route, Trees, Waypoints } from 'lucide-react';
import './placement-scenario.css';
import type { PlacementMaskPreset } from '@green/api-client';

export type PlacementScenarioId = 'natural' | PlacementMaskPreset['id'];

const SCENARIOS: ReadonlyArray<{
  id: PlacementScenarioId;
  title: string;
  description: string;
  icon: typeof Trees;
}> = [
  {
    id: 'natural',
    icon: Waypoints,
    title: 'Свободно',
    description: 'Естественно заполняет подходящие места без жёсткого ритма',
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

export function PlacementScenarioPicker({ value, presets, disabled = false, onChange }: {
  value: PlacementScenarioId;
  presets?: PlacementMaskPreset[];
  disabled?: boolean;
  onChange: (value: PlacementScenarioId) => void;
}) {
  const descriptionPrefix = useId();
  const groupName = `${descriptionPrefix}-placement-scenario`;
  const presetsById = new Map(presets?.map((preset) => [preset.id, preset]));

  return <fieldset className="editor-scenarios">
    <legend>Размещение</legend>
    <div className="editor-scenarios__grid">
      {SCENARIOS.map(scenario => {
        const preset = scenario.id === 'natural' ? undefined : presetsById.get(scenario.id);
        const unavailable = preset?.available === false;
        const description = unavailable ? preset.unavailable_reason ?? 'Недоступно для этого чертежа' : preset?.description ?? scenario.description;
        const title = preset?.title ?? scenario.title;
        const Icon = scenario.icon;
        return <label key={scenario.id} className="editor-scenario" title={description} data-selected={value === scenario.id || undefined} data-unavailable={unavailable || undefined}>
          <input type="radio" name={groupName} value={scenario.id} checked={value === scenario.id} disabled={disabled || unavailable} aria-label={title} aria-describedby={`${descriptionPrefix}-${scenario.id}`} onChange={() => { if (!disabled && !unavailable) onChange(scenario.id); }} />
          <Icon size={15} /><span>{title}</span>
          <span id={`${descriptionPrefix}-${scenario.id}`} className="sr-only">{description}</span>
        </label>;
      })}
    </div>
    {presets?.filter(preset => !preset.available).map(preset => <p key={preset.id} className="editor-panel__hint">{preset.title}: {preset.unavailable_reason ?? 'Нет необходимых данных'}</p>)}
  </fieldset>;
}
