import type {
  RenderCamera,
  SceneSnapshot,
  SceneRenderPreparation,
} from '@green/api-client';
import {
  useSceneRender,
  sceneRenderFileUrl,
} from '@/features/scene-render/model/useSceneRender';
import {
  Button,
  IconButton,
  Dialog,
  InlineMessage,
  LinkButton,
  Progress,
  SurfaceHeader,
  type ButtonProps,
} from '@green/ui';
import { Camera, Download, RefreshCw, Square, X } from 'lucide-react';
import { useState } from 'react';
import { useClock } from '@/shared/time/useClock';
import { CLOCK_INTERVAL_MS, formatDuration } from '@/shared/time/duration';
import { SceneRenderImageViewer } from './SceneRenderImageViewer';
import { SceneRenderDetails } from './SceneRenderDetails';

type SceneRenderControlProps = {
  snapshot: SceneSnapshot;
  ready: boolean;
  getCamera: () => RenderCamera | undefined;
  preparation?: SceneRenderPreparation;
  className?: string;
  variant?: ButtonProps['variant'];
  operation?: ReturnType<typeof useSceneRender>;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  backgroundIndicator?: boolean;
};

export function SceneRenderControl(props: SceneRenderControlProps) {
  return props.operation ? (
    <SceneRenderControlView {...props} operation={props.operation} />
  ) : (
    <OwnedSceneRenderControl {...props} />
  );
}
function OwnedSceneRenderControl(props: SceneRenderControlProps) {
  const operation = useSceneRender(
    props.snapshot,
    props.getCamera,
    props.preparation,
  );
  return <SceneRenderControlView {...props} operation={operation} />;
}
function SceneRenderControlView({
  snapshot,
  ready,
  operation,
  className,
  variant = 'secondary',
  open: controlledOpen,
  onOpenChange,
  backgroundIndicator = true,
}: SceneRenderControlProps & { operation: ReturnType<typeof useSceneRender> }) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const setOpen = (next: boolean) => {
    if (controlledOpen === undefined) setInternalOpen(next);
    onOpenChange?.(next);
  };
  const {
    start,
    status,
    cancel,
    job,
    busy,
    error,
    matchesCurrentView,
    renderContext,
  } = operation;
  const tick = useClock(CLOCK_INTERVAL_MS, busy && open);
  // A hidden dialog has no display timer; catch up on its first reopened frame.
  const now = open ? Math.max(tick, Date.now()) : tick;
  const canStart = ready && !!snapshot.content_sha256;
  const completed = !busy && !start.error && job?.status === 'completed';
  const failed = !busy && (start.error || job?.status === 'failed');
  const hasImage = completed && !!job.image_url;
  const previousView = completed && !matchesCurrentView();
  const launch = () => {
    cancel.reset();
    start.mutate();
  };
  const stage = cancel.isPending
    ? 'Отменяем рендер'
    : start.isPending
      ? 'Подготовка рендера'
      : job?.stage || 'Ожидаем рендер';
  return (
    <>
      <IconButton
        className={`data-[busy]:cursor-progress ${className ?? ''}`}
        data-busy={busy || undefined}
        variant={variant}
        icon={
          <Camera
            className={
              failed || error
                ? 'text-error'
                : busy && !open && backgroundIndicator
                  ? 'motion-safe:animate-pulse'
                  : undefined
            }
          />
        }
        label={busy ? 'Рендер выполняется' : 'Рендер'}
        aria-description={failed || error ? 'Ошибка рендера' : undefined}
        disabled={!canStart && !start.submittedAt && !busy}
        onClick={() => {
          setOpen(true);
          // Opening an existing result is read-only, even after moving the camera.
          if (canStart && !start.submittedAt && !busy) launch();
        }}
      />
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Рендер сцены"
        size={hasImage ? 'wide' : 'default'}
        stableHeight={hasImage}
        bodyInset="none"
        footer={
          !hasImage && (
            <div className="flex min-w-0 items-center justify-between gap-3">
              <div>
                {busy && job && (
                  <Button
                    variant="secondary"
                    controlSize="compact"
                    startIcon={<Square />}
                    loading={cancel.isPending}
                    loadingIndicator={false}
                    onClick={() => cancel.mutate()}
                  >
                    Отменить рендер
                  </Button>
                )}
                {failed && (
                  <Button
                    variant="secondary"
                    controlSize="compact"
                    startIcon={<RefreshCw />}
                    disabled={!canStart}
                    onClick={launch}
                  >
                    Повторить рендер
                  </Button>
                )}
              </div>
              <Button
                variant="secondary"
                controlSize="compact"
                onClick={() => setOpen(false)}
              >
                {busy ? 'Скрыть' : 'Закрыть'}
              </Button>
            </div>
          )
        }
        header={
          <SurfaceHeader className="border-0 border-b border-solid py-2">
            <div className="flex min-w-0 items-baseline gap-3">
              <h2 className="m-0 shrink-0 text-base font-semibold">Рендер</h2>
              <span className="truncate text-xs text-neutral-500">
                {previousView ? 'Предыдущий ракурс' : ''}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-4">
              <div className="flex items-center gap-2">
                {job && !start.isPending && !hasImage && (
                  <SceneRenderDetails job={job} context={renderContext} />
                )}
                {!busy && !failed && (
                  <IconButton
                    icon={Camera}
                    label="Новый рендер"
                    variant="ghost"
                    controlSize="compact"
                    disabled={!canStart}
                    onClick={launch}
                  />
                )}
                {hasImage && (
                  <LinkButton
                    variant="secondary"
                    controlSize="compact"
                    startIcon={<Download />}
                    href={sceneRenderFileUrl(job.image_url!)}
                    download
                  >
                    Скачать PNG
                  </LinkButton>
                )}
              </div>
              <IconButton
                icon={X}
                label={
                  hasImage
                    ? 'Закрыть'
                    : busy
                      ? 'Скрыть окно рендера'
                      : 'Закрыть окно рендера'
                }
                variant="ghost"
                controlSize="compact"
                onClick={() => setOpen(false)}
              />
            </div>
          </SurfaceHeader>
        }
      >
        <div className="flex min-h-0 flex-1 flex-col">
          {error && (
            <div className="border-b border-neutral-200 p-4">
              <InlineMessage
                tone="error"
                title={
                  start.error
                    ? 'Не удалось запустить рендер'
                    : status.error
                      ? 'Не удалось обновить состояние рендера'
                      : 'Не удалось отменить рендер'
                }
              >
                <p className="m-0 break-words">{error.message}</p>
                {!!status.error && !start.error && (
                  <Button
                    className="mt-2"
                    controlSize="compact"
                    startIcon={<RefreshCw />}
                    loading={status.isFetching}
                    loadingIndicator={false}
                    onClick={() => void status.refetch()}
                  >
                    Обновить статус
                  </Button>
                )}
              </InlineMessage>
            </div>
          )}
          {hasImage ? (
            <SceneRenderImageViewer
              key={job.id}
              src={sceneRenderFileUrl(job.image_url!)}
              width={job.width}
              height={job.height}
              details={<SceneRenderDetails job={job} context={renderContext} />}
            />
          ) : (
            <div className="grid min-w-0 gap-3 p-4">
              <div className="grid w-full min-w-0 gap-3">
                {busy ? (
                  <>
                    <div role="status">
                      <Progress
                        label={
                          status.error ? 'Ожидаем связь с рендером' : stage
                        }
                        valueLabel={`С запуска ${formatDuration(now - start.submittedAt)}`}
                      />
                    </div>
                  </>
                ) : job?.status === 'failed' && !start.error ? (
                  <InlineMessage
                    tone="error"
                    title="Не удалось создать изображение"
                  >
                    {job.message || 'Повторите рендер текущего ракурса'}
                  </InlineMessage>
                ) : !error ? (
                  <p role="status" className="m-0 text-sm text-neutral-600">
                    {job?.status === 'cancelled'
                      ? 'Рендер отменён'
                      : completed
                        ? 'Изображение недоступно'
                        : 'Изображение ещё не создано'}
                  </p>
                ) : null}
              </div>
            </div>
          )}
        </div>
      </Dialog>
    </>
  );
}
