import type { SceneRenderPreparation } from '@green/api-client';
import {
  Button,
  IconButton,
  Disclosure,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from '@green/ui';
import {
  Check,
  ChevronDown,
  Info,
  MapPin,
  RefreshCw,
  Box,
  CircleAlert,
} from 'lucide-react';
import { useState } from 'react';
import { SceneEnvironmentErrorMessage } from './SceneEnvironmentProgress';

export function SceneEnvironmentControl({
  labelled = false,
  preparation,
  loading,
  applying,
  error,
  canRefresh,
  onRefresh,
  onData,
  onLocation,
  operationLabel,
  quietStatus = false,
}: {
  quietStatus?: boolean;
  labelled?: boolean;
  operationLabel?: string;
  preparation?: SceneRenderPreparation;
  loading: boolean;
  applying: boolean;
  startedAt?: number;
  error?: string;
  canRefresh: boolean;
  onRefresh: () => void;
  onData: () => void;
  onLocation?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const busy = loading || applying;
  const shownPreparation = canRefresh ? preparation : undefined;
  return (
    <>
      {!quietStatus && (
        <span
          className="sr-only"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          {error
            ? 'Ошибка загрузки 3D'
            : busy
              ? operationLabel || 'Открываем 3D'
              : shownPreparation
                ? '3D готов'
                : ''}
        </span>
      )}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          render={
            labelled ? (
              <Button
                variant="ghost"
                controlSize="compact"
                className="w-[calc(6rem+var(--spacing)+var(--control-height-sm))] justify-between border-0 px-2 text-left data-[busy]:cursor-progress data-[popup-open]:bg-neutral-100"
                endIcon={
                  <ChevronDown
                    className={busy ? 'motion-safe:animate-pulse' : undefined}
                  />
                }
                aria-label="Окружение и данные 3D"
                data-busy={busy || undefined}
              >
                Окружение
              </Button>
            ) : (
              <IconButton
                data-busy={busy || undefined}
                variant="ghost"
                controlSize="compact"
                icon={
                  error ? (
                    <CircleAlert className="text-error" />
                  ) : (
                    <Box
                      className={busy ? 'motion-safe:animate-pulse' : undefined}
                    />
                  )
                }
                label={
                  error
                    ? '3D: ошибка загрузки'
                    : busy
                      ? `3D: ${operationLabel || 'открываем сцену'}`
                      : '3D: сцена и данные'
                }
              />
            )
          }
        />
        <PopoverPopup
          size="default"
          align="start"
          alignOffset={-5}
          sideOffset={13}
          className="rounded-control grid cursor-default p-0 shadow-none [scrollbar-gutter:auto]"
        >
          <div className="grid gap-3 p-4">
            <PopoverTitle className="m-0 text-sm font-semibold">
              3D
            </PopoverTitle>
            {busy ? (
              <p
                role="status"
                className="m-0 text-xs leading-4 text-neutral-600"
              >
                {operationLabel || 'Открываем 3D'}
              </p>
            ) : !canRefresh ? (
              <p className="m-0 text-xs leading-4 text-neutral-600">
                Ожидает готовности текущей сцены
              </p>
            ) : !error ? (
              <p className="m-0 flex items-center gap-2 text-xs leading-4 text-neutral-600">
                {shownPreparation && (
                  <Check aria-hidden="true" className="size-4 shrink-0" />
                )}
                {shownPreparation ? '3D готов' : '3D готовится'}
              </p>
            ) : null}
            {error && (
              <div role="alert" className="grid gap-1 text-xs leading-4">
                <p className="text-error m-0 font-medium">
                  {shownPreparation
                    ? 'Не удалось обновить окружение'
                    : 'Окружение недоступно'}
                </p>
                <p className="m-0 break-words text-neutral-600">
                  <SceneEnvironmentErrorMessage error={error} />
                </p>
              </div>
            )}
          </div>
          {shownPreparation && (
            <section
              aria-label="Состав показанного окружения"
              className="grid gap-3 border-0 border-t border-solid border-neutral-200 p-4"
            >
              <div className="grid gap-1">
                <h3 className="m-0 text-xs leading-4 font-medium">
                  {busy || error
                    ? 'На сцене: предыдущее окружение'
                    : 'На сцене'}
                </h3>
                {error && !busy && (
                  <p className="m-0 text-xs leading-4 text-neutral-600">
                    Рендер доступен.
                  </p>
                )}
              </div>
              <dl className="m-0 grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-2 text-xs leading-4 [&_dd]:m-0 [&_dd]:text-right [&_dd]:tabular-nums [&_dt]:text-neutral-600">
                <dt>Деревья окружения</dt>
                <dd>
                  {shownPreparation.summary.context_tree_count.toLocaleString(
                    'ru-RU',
                  )}
                </dd>
                <dt>Соседние здания</dt>
                <dd>
                  {shownPreparation.summary.map_building_count.toLocaleString(
                    'ru-RU',
                  )}
                </dd>
                <dt>Высоты из OSM</dt>
                <dd>
                  {shownPreparation.summary.osm_building_count.toLocaleString(
                    'ru-RU',
                  )}
                </dd>
                <dt>Условные объёмы</dt>
                <dd>
                  {shownPreparation.summary.conditional_building_count.toLocaleString(
                    'ru-RU',
                  )}
                </dd>
              </dl>
              <Disclosure
                title="Сведения о подготовке"
                variant="plain"
                controlSize="compact"
              >
                <p className="m-0 text-xs leading-4 text-neutral-600">
                  {shownPreparation.summary.message}
                </p>
              </Disclosure>
            </section>
          )}
          <div className="grid gap-1 border-0 border-t border-solid border-neutral-200 p-1">
            {onLocation && (
              <Button
                variant="ghost"
                controlSize="compact"
                className="w-full justify-start border-0"
                icon={MapPin}
                onClick={() => {
                  setOpen(false);
                  onLocation();
                }}
              >
                Местоположение проекта
              </Button>
            )}
            <Button
              variant="ghost"
              controlSize="compact"
              className="w-full justify-start border-0"
              icon={RefreshCw}
              aria-label={error ? 'Повторить загрузку 3D' : 'Обновить 3D'}
              disabled={!canRefresh || busy}
              onClick={onRefresh}
            >
              {error ? 'Повторить загрузку' : 'Обновить 3D'}
            </Button>
            <Button
              variant="ghost"
              controlSize="compact"
              className="w-full justify-start border-0"
              icon={Info}
              onClick={() => {
                setOpen(false);
                onData();
              }}
            >
              Данные и управление 3D
            </Button>
          </div>
        </PopoverPopup>
      </Popover>
    </>
  );
}
