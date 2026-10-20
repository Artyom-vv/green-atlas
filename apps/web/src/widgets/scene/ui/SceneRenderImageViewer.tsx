import { Button, IconButton, Text } from '@green/ui';
import { Minus, Plus, RefreshCw, Scan } from 'lucide-react';
import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from 'react';
import {
  clampImageView,
  fitImageScale,
  imagePanLimits,
  MAX_IMAGE_SCALE,
  zoomImageView,
  type ImageSize,
  type ImageView,
} from './SceneRenderImageViewer.geometry';

export interface SceneRenderImageViewerProps {
  src: string;
  width: number;
  height: number;
  details?: ReactNode;
}

export function SceneRenderImageViewer(props: SceneRenderImageViewerProps) {
  return <ImageViewer key={props.src} {...props} />;
}

function ImageViewer({
  src,
  width,
  height,
  details,
}: SceneRenderImageViewerProps) {
  const instructionId = useId();
  const viewportRef = useRef<HTMLDivElement>(null);
  const [image, setImage] = useState<ImageSize>({
    width: Math.max(1, width),
    height: Math.max(1, height),
  });
  const [viewport, setViewport] = useState<ImageSize>({ width: 0, height: 0 });
  const [view, setView] = useState<ImageView>({
    mode: 'fit',
    scale: 1,
    x: 0,
    y: 0,
  });
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>(
    'loading',
  );
  const [attempt, setAttempt] = useState(0);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{
    pointer: number;
    startX: number;
    startY: number;
    imageX: number;
    imageY: number;
  } | null>(null);
  const actual = clampImageView(view, image, viewport);
  const limits = imagePanLimits(image, viewport, actual.scale);
  const canPan = status === 'ready' && (limits.x > 0 || limits.y > 0);
  const ready = status === 'ready' && viewport.width > 0 && viewport.height > 0;

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const measure = () => {
      const { width: nextWidth, height: nextHeight } =
        element.getBoundingClientRect();
      const next = { width: nextWidth, height: nextHeight };
      setViewport(next);
      if (nextWidth > 0 && nextHeight > 0)
        setView((previous) => clampImageView(previous, image, next));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [image]);

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      if (
        status !== 'ready' ||
        viewport.width <= 0 ||
        viewport.height <= 0 ||
        event.deltaY === 0
      )
        return;
      event.preventDefault();
      if (drag.current) return;
      const bounds = element.getBoundingClientRect();
      const anchor = {
        x: event.clientX - bounds.left - viewport.width / 2,
        y: event.clientY - bounds.top - viewport.height / 2,
      };
      const pixels =
        event.deltaY *
        (event.deltaMode === 1
          ? 16
          : event.deltaMode === 2
            ? viewport.height
            : 1);
      const factor = Math.exp(-Math.max(-100, Math.min(100, pixels)) * 0.0025);
      setView((previous) => {
        const current = clampImageView(previous, image, viewport);
        return zoomImageView(
          current,
          current.scale * factor,
          anchor,
          image,
          viewport,
        );
      });
    };
    element.addEventListener('wheel', onWheel, { passive: false });
    return () => element.removeEventListener('wheel', onWheel);
  }, [image, viewport, status]);

  const fit = () =>
    setView({ mode: 'fit', scale: fitImageScale(image, viewport), x: 0, y: 0 });
  const zoom = (scale: number) =>
    setView(zoomImageView(actual, scale, { x: 0, y: 0 }, image, viewport));
  const endDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointer !== event.pointerId) return;
    drag.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture?.(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (
      !ready ||
      event.target !== event.currentTarget ||
      event.ctrlKey ||
      event.metaKey ||
      event.altKey
    )
      return;
    if (event.key === '+' || event.key === '=') zoom(actual.scale * 1.25);
    else if (event.key === '-') zoom(actual.scale / 1.25);
    else if (event.key === '0') fit();
    else if (event.key === '1') zoom(1);
    else if (
      ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)
    ) {
      if (!canPan) return;
      const step = event.shiftKey ? 120 : 40;
      setView(
        clampImageView(
          {
            ...actual,
            mode: 'custom',
            x:
              actual.x +
              (event.key === 'ArrowLeft'
                ? step
                : event.key === 'ArrowRight'
                  ? -step
                  : 0),
            y:
              actual.y +
              (event.key === 'ArrowUp'
                ? step
                : event.key === 'ArrowDown'
                  ? -step
                  : 0),
          },
          image,
          viewport,
        ),
      );
    } else return;
    event.preventDefault();
    event.stopPropagation();
  };

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-neutral-100">
      <p id={instructionId} className="sr-only">
        Масштаб: колесо мыши, плюс и минус. Ноль — вписать, один — 100%.
        Увеличенное изображение можно перемещать мышью или клавишами со
        стрелками.
      </p>
      <div
        ref={viewportRef}
        role="region"
        aria-label="Просмотр рендера"
        aria-describedby={instructionId}
        aria-busy={status === 'loading'}
        tabIndex={0}
        className={`relative min-h-0 flex-1 overflow-hidden outline-none focus-visible:ring-2 focus-visible:ring-(--focus) focus-visible:ring-inset ${canPan ? (dragging ? 'cursor-grabbing touch-none select-none' : 'cursor-grab touch-none select-none') : status === 'loading' ? 'cursor-progress' : 'cursor-default'}`}
        onKeyDown={onKeyDown}
        onDoubleClick={() => {
          if (ready) {
            if (actual.mode === 'fit') zoom(1);
            else fit();
          }
        }}
        onPointerDown={(event) => {
          if (!canPan || event.button !== 0 || drag.current) return;
          event.preventDefault();
          event.currentTarget.focus();
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = {
            pointer: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            imageX: actual.x,
            imageY: actual.y,
          };
          setDragging(true);
        }}
        onPointerMove={(event) => {
          const start = drag.current;
          if (!start || start.pointer !== event.pointerId) return;
          setView(
            clampImageView(
              {
                ...actual,
                mode: 'custom',
                x: start.imageX + event.clientX - start.startX,
                y: start.imageY + event.clientY - start.startY,
              },
              image,
              viewport,
            ),
          );
        }}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onLostPointerCapture={endDrag}
      >
        {status === 'error' ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-4 text-center">
            <Text as="p" role="alert">
              Не удалось загрузить PNG
            </Text>
            <Button
              controlSize="compact"
              startIcon={<RefreshCw />}
              onClick={() => {
                setStatus('loading');
                setAttempt((value) => value + 1);
              }}
            >
              Повторить загрузку
            </Button>
          </div>
        ) : (
          <img
            key={attempt}
            src={src}
            alt="Рендер 3D-сцены"
            draggable={false}
            className={`absolute top-1/2 left-1/2 max-w-none select-none ${status === 'loading' ? 'invisible' : ''}`}
            style={{
              width: image.width * actual.scale,
              height: image.height * actual.scale,
              transform: `translate(calc(-50% + ${actual.x}px), calc(-50% + ${actual.y}px))`,
            }}
            onLoad={(event) => {
              const target = event.currentTarget;
              if (target.naturalWidth > 0 && target.naturalHeight > 0)
                setImage({
                  width: target.naturalWidth,
                  height: target.naturalHeight,
                });
              setView({ mode: 'fit', scale: 1, x: 0, y: 0 });
              setStatus('ready');
            }}
            onError={() => {
              setStatus('error');
              drag.current = null;
              setDragging(false);
            }}
          />
        )}
        {status === 'loading' && (
          <div className="absolute inset-0 flex items-center justify-center p-4">
            <Text role="status" tone="muted">
              Загружаем PNG
            </Text>
          </div>
        )}
      </div>
      <div className="flex min-h-12 shrink-0 items-center justify-between gap-4 border-t border-neutral-200 bg-white px-4 py-2">
        <div className="flex min-w-0 items-center gap-1">
          <Text tone="muted" className="min-w-0 truncate text-xs tabular-nums">
            {image.width} × {image.height} px
          </Text>
          {details}
        </div>
        <div
          className="flex shrink-0 items-center gap-1"
          role="group"
          aria-label="Масштаб изображения"
        >
          <IconButton
            icon={Minus}
            label="Уменьшить изображение"
            variant="ghost"
            controlSize="compact"
            disabled={
              !ready ||
              actual.scale <= Math.min(0.1, fitImageScale(image, viewport))
            }
            onClick={() => zoom(actual.scale / 1.25)}
          />
          <Text
            className="w-12 text-center text-xs tabular-nums"
            aria-label={`Масштаб ${Math.round(actual.scale * 100)}%`}
          >
            {Math.round(actual.scale * 100)}%
          </Text>
          <IconButton
            icon={Plus}
            label="Увеличить изображение"
            variant="ghost"
            controlSize="compact"
            disabled={!ready || actual.scale >= MAX_IMAGE_SCALE}
            onClick={() => zoom(actual.scale * 1.25)}
          />
          <Button
            variant="ghost"
            controlSize="compact"
            disabled={!ready}
            onClick={() => zoom(1)}
            aria-label="Исходный размер, 100%"
          >
            100%
          </Button>
          <IconButton
            icon={Scan}
            label="Вписать изображение"
            variant="ghost"
            controlSize="compact"
            disabled={!ready}
            onClick={fit}
          />
        </div>
      </div>
    </div>
  );
}
