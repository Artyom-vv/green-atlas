import type { FC } from 'react';
import { Button, InlineMessage, Text } from '@green/ui';
import type { SceneReviewOptions } from '../model/sceneContracts';
import { sceneHorizonLabel } from '../model/scenePresentation';

export interface SceneStatusProps extends Pick<
  SceneReviewOptions,
  'horizon' | 'loading' | 'error'
> {
  snapshotHorizon?: number;
  modelsReady: boolean;
  renderError?: string;
  onRetry: () => void;
}

export const SceneStatus: FC<SceneStatusProps> = ({
  horizon,
  snapshotHorizon,
  loading,
  error,
  modelsReady,
  renderError,
  onRetry,
}) => {
  const shownHorizonNote =
    modelsReady &&
    !renderError &&
    snapshotHorizon !== undefined &&
    snapshotHorizon !== horizon
      ? `На сцене: ${sceneHorizonLabel(snapshotHorizon)}. Запрошено: ${sceneHorizonLabel(horizon)}.`
      : undefined;
  const showStatus = Boolean(
    (!modelsReady || loading || shownHorizonNote) && !renderError && !error,
  );
  return (
    <div className="absolute top-4 left-18 z-2 grid w-[min(28rem,calc(100%-11rem))] gap-2">
      {showStatus && (
        <div
          className="rounded-card grid gap-1 border border-neutral-300 bg-white px-3 py-2 text-xs text-neutral-700"
          role="status"
        >
          <span>
            {!modelsReady
              ? 'Загружаем модели растений'
              : loading
                ? snapshotHorizon !== undefined
                  ? 'Обновляем прогноз'
                  : 'Загружаем сцену'
                : 'Показан предыдущий прогноз'}
          </span>
          {Boolean(shownHorizonNote) && (
            <Text as="p" variant="caption">
              {shownHorizonNote}
            </Text>
          )}
        </div>
      )}
      {Boolean(error || renderError) && (
        <div className="grid gap-2">
          <InlineMessage tone="error">{error ?? renderError}</InlineMessage>
          {Boolean(shownHorizonNote) && (
            <div className="rounded-card border border-neutral-300 bg-white px-3 py-2">
              <Text as="p" variant="caption">
                {shownHorizonNote}
              </Text>
            </div>
          )}
          {Boolean(renderError) && (
            <Button variant="secondary" onClick={onRetry}>
              Повторить запуск 3D
            </Button>
          )}
        </div>
      )}
    </div>
  );
};
