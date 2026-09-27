import type { ScenePlantObject, ValidationIssue } from '@green/api-client';
import { Text } from '@green/ui';
import type { CSSProperties, FC } from 'react';
import type { SceneHoverPresentation } from '@/widgets/scene/model/sceneContracts';
import {
  SCENE_GROWTH_LABELS,
  sceneHorizonLabel,
} from '@/widgets/scene/model/scenePresentation';

export interface SceneHoverCardProps {
  position: SceneHoverPresentation;
  object: ScenePlantObject;
  issue?: ValidationIssue;
}

export const SceneHoverCard: FC<SceneHoverCardProps> = ({
  position,
  object,
  issue,
}) => (
  <div
    className="rounded-card pointer-events-none absolute top-[max(0.5rem,min(var(--scene-hover-y),calc(100%-7rem)))] left-[max(0.5rem,min(var(--scene-hover-x),calc(100%-18rem)))] z-3 grid w-max max-w-[min(28ch,calc(100%-2rem))] gap-1 border border-neutral-300 bg-white p-3 text-xs text-neutral-700 shadow-sm"
    style={
      {
        '--scene-hover-x': `${position.x + 18}px`,
        '--scene-hover-y': `${position.y + 18}px`,
      } as CSSProperties
    }
    aria-hidden="true"
  >
    <Text as="strong" variant="label">
      {object.common_name ?? (object.kind === 'tree' ? 'Дерево' : 'Кустарник')}
    </Text>
    <span>
      {SCENE_GROWTH_LABELS[object.growth_stage] ?? 'Стадия не определена'}
    </span>
    <span className="first-letter:uppercase">
      {sceneHorizonLabel(object.forecast_horizon_year ?? 0)}
    </span>
    {issue ? (
      <Text
        variant="caption"
        className={
          issue.severity === 'error' ? 'text-red-700' : 'text-amber-700'
        }
      >
        {issue.severity === 'error' ? 'Ошибка' : 'Риск'}: {issue.title}
      </Text>
    ) : null}
  </div>
);
