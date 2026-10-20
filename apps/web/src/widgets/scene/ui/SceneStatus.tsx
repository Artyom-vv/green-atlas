import type { FC, ReactNode } from 'react';
import { Button, Icon, type IconNode } from '@green/ui';
import { Box, CircleAlert, RefreshCw } from 'lucide-react';
import type { SceneReviewOptions } from '../model/sceneContracts';
import { sceneHorizonLabel } from '../model/scenePresentation';

/** The overlay layout owns placement; each message owns its content and action. */
export function SceneNotice({
  title,
  children,
  action,
  error = false,
  busy = false,
  icon = Box,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  error?: boolean;
  busy?: boolean;
  icon?: IconNode;
}) {
  return (
    <div
      className="rounded-control grid w-fit max-w-full grid-cols-[1rem_minmax(0,1fr)] items-start gap-x-2 gap-y-2 border border-neutral-300 bg-white p-3 text-xs leading-4"
      role={error ? 'alert' : 'status'}
    >
      <Icon
        icon={error ? CircleAlert : icon}
        className={
          error
            ? 'text-error'
            : busy
              ? 'animate-pulse text-neutral-600 motion-reduce:animate-none'
              : 'text-neutral-600'
        }
      />
      <div className="grid min-w-0 gap-1 break-words">
        <strong className="font-medium text-neutral-800">{title}</strong>
        {children && <div className="text-neutral-600">{children}</div>}
      </div>
      {action && (
        <div className="col-start-2 flex flex-wrap gap-2">{action}</div>
      )}
    </div>
  );
}

export interface SceneStatusProps extends Pick<
  SceneReviewOptions,
  'horizon' | 'loading' | 'error' | 'onRetryScene'
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
  onRetryScene,
}) => {
  const shownHorizonNote =
    modelsReady &&
    !renderError &&
    snapshotHorizon !== undefined &&
    snapshotHorizon !== horizon
      ? `На сцене: ${sceneHorizonLabel(snapshotHorizon)}. Запрошено: ${sceneHorizonLabel(horizon)}`
      : undefined;
  if (error || renderError) {
    return (
      <SceneNotice
        title={
          renderError ? 'Не удалось запустить 3D' : 'Не удалось обновить сцену'
        }
        error
        action={
          (renderError || onRetryScene) && (
            <Button
              variant="secondary"
              icon={RefreshCw}
              onClick={renderError ? onRetry : onRetryScene}
              aria-label={
                renderError ? 'Повторить запуск 3D' : 'Повторить загрузку сцены'
              }
            >
              Повторить
            </Button>
          )
        }
      >
        <p className="m-0">{renderError ?? error}</p>
        {shownHorizonNote && <p className="mt-2 mb-0">{shownHorizonNote}</p>}
      </SceneNotice>
    );
  }
  if (modelsReady && !loading && !shownHorizonNote) return null;
  return (
    <SceneNotice
      busy={!modelsReady || Boolean(loading)}
      title={
        !modelsReady
          ? 'Загружаем модели растений'
          : loading
            ? snapshotHorizon !== undefined
              ? 'Обновляем прогноз'
              : 'Загружаем сцену'
            : 'Показан предыдущий прогноз'
      }
    >
      {shownHorizonNote}
    </SceneNotice>
  );
};
