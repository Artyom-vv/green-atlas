import type { LocationBounds, LocationPoint } from '@green/api-client';
import { IconButton } from '@green/ui';
import { Maximize, Minus, Plus } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { boundsOf } from '@/features/project-location/model/locationGeometry';
import { CanvasControlGroup } from '@/shared/ui/CanvasControls';

interface Marker {
  point: LocationPoint;
  number: number;
  holdout: boolean;
}

export interface LocationPlanProps {
  title: string;
  lines: LocationPoint[][];
  overlay?: LocationPoint[][];
  streets?: LocationPoint[][];
  boundary?: LocationPoint[][];
  fitBounds?: LocationBounds;
  markers?: Marker[];
  activePair?: number;
  onPoint?: (point: LocationPoint) => void;
  disabled?: boolean;
  className?: string;
}

interface Camera {
  center: LocationPoint;
  width: number;
}

const EMPTY: LocationPoint[][] = [];

export function LocationPlan({
  title,
  lines,
  overlay = EMPTY,
  streets = EMPTY,
  boundary = EMPTY,
  fitBounds,
  markers = [],
  activePair,
  onPoint,
  disabled = false,
  className = '',
}: LocationPlanProps) {
  const id = useId();
  const container = useRef<HTMLElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ width: 480, height: 320 });
  const [camera, setCamera] = useState<Camera>();
  const [cursor, setCursor] = useState<LocationPoint>();
  const [dragging, setDragging] = useState(false);
  const drag = useRef<
    | {
        pointerId: number;
        client: LocationPoint;
        camera: Camera;
        scale: number;
        moved: boolean;
      }
    | undefined
  >(undefined);
  const canPick = !disabled && Boolean(onPoint);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const measure = () => {
      const rect = element.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setSize((previous) =>
          previous.width === rect.width && previous.height === rect.height
            ? previous
            : { width: rect.width, height: rect.height },
        );
      }
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const initial = useMemo<Camera>(() => {
    const bounds = fitBounds ??
      boundsOf([...lines, ...streets, ...boundary]) ??
      boundsOf(overlay) ?? [-50, -50, 50, 50];
    return {
      center: [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2],
      width:
        Math.max(
          20,
          bounds[2] - bounds[0],
          ((bounds[3] - bounds[1]) * size.width) / size.height,
        ) * 1.16,
    };
  }, [fitBounds, lines, streets, boundary, overlay, size]);

  useEffect(() => {
    const element = svg.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      if (event.deltaY === 0) return;
      event.preventDefault();
      if (drag.current) return;
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const x = (event.clientX - rect.left) / rect.width - 0.5;
      const y = 0.5 - (event.clientY - rect.top) / rect.height;
      const delta =
        event.deltaY *
        (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1);
      const factor = Math.exp(Math.max(-100, Math.min(100, delta)) * 0.0025);
      setCamera((previous) => {
        const before = previous ?? initial;
        const width = Math.max(
          0.4,
          Math.min(40_000_000, before.width * factor),
        );
        const difference = before.width - width;
        return {
          width,
          center: [
            before.center[0] + x * difference,
            before.center[1] + (y * difference * size.height) / size.width,
          ],
        };
      });
    };
    element.addEventListener('wheel', onWheel, { passive: false });
    return () => element.removeEventListener('wheel', onWheel);
  }, [initial, size]);

  const previousPair = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (activePair === previousPair.current) return;
    previousPair.current = activePair;
    setCursor(undefined);
    const selected = markers.find(
      (marker) => marker.number === (activePair ?? -1) + 1,
    );
    if (selected)
      setCamera((previous) => ({
        width: (previous ?? initial).width,
        center: selected.point,
      }));
  }, [activePair, markers, initial]);

  const current = camera ?? initial;
  const worldHeight = (current.width * size.height) / size.width;
  const viewport: LocationBounds = [
    current.center[0] - current.width / 2,
    current.center[1] - worldHeight / 2,
    current.center[0] + current.width / 2,
    current.center[1] + worldHeight / 2,
  ];
  const scale = size.width / current.width;
  const screenPoint = ([x, y]: LocationPoint): LocationPoint => [
    (x - viewport[0]) * scale,
    (viewport[3] - y) * scale,
  ];
  const currentCursor = cursor ?? current.center;
  const cursorScreen = screenPoint(currentCursor);
  const path = (line: LocationPoint[]) =>
    line
      .map(
        (point, index) => `${index ? 'L' : 'M'}${screenPoint(point).join(',')}`,
      )
      .join(' ');
  const fit = () => {
    setCamera(undefined);
    setCursor(undefined);
  };
  const zoom = (factor: number) =>
    setCamera({
      center: current.center,
      width: Math.max(0.4, Math.min(40_000_000, current.width * factor)),
    });
  const pointAt = (
    clientX: number,
    clientY: number,
    element: SVGSVGElement,
  ): LocationPoint => {
    const rect = element.getBoundingClientRect();
    return [
      viewport[0] + ((clientX - rect.left) / rect.width) * current.width,
      viewport[3] - ((clientY - rect.top) / rect.height) * worldHeight,
    ];
  };
  const finishDrag = (element: SVGSVGElement, pointerId: number) => {
    drag.current = undefined;
    setDragging(false);
    if (element.hasPointerCapture?.(pointerId))
      element.releasePointerCapture(pointerId);
  };

  return (
    <section
      ref={container}
      aria-label={title}
      className={`relative flex min-h-0 min-w-0 flex-1 overflow-hidden bg-neutral-50 ${className}`}
    >
      <p id={`${id}-hint`} className="sr-only">
        Колесо мыши, плюс и минус изменяют масштаб, Home показывает весь план
        Перетаскивание перемещает план Стрелки перемещают указатель, Shift со
        стрелками перемещает план, Alt уменьшает шаг Enter выбирает точку, если
        выбор доступен
      </p>
      <svg
        ref={svg}
        viewBox={`0 0 ${size.width} ${size.height}`}
        preserveAspectRatio="xMidYMid meet"
        role="application"
        aria-label={`План: ${title}`}
        aria-describedby={`${id}-hint`}
        tabIndex={0}
        className={`absolute inset-0 block size-full touch-none outline-offset-[-2px] select-none focus-visible:outline-2 focus-visible:outline-(--focus) ${dragging ? 'cursor-grabbing' : canPick ? 'cursor-crosshair' : 'cursor-grab'}`}
        onFocus={() => {
          if (canPick) setCursor((previous) => previous ?? current.center);
        }}
        onPointerDown={(event) => {
          if (event.button !== 0 || drag.current) return;
          event.preventDefault();
          event.currentTarget.focus();
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = {
            pointerId: event.pointerId,
            client: [event.clientX, event.clientY],
            camera: current,
            scale:
              event.currentTarget.getBoundingClientRect().width / current.width,
            moved: false,
          };
          setDragging(true);
        }}
        onPointerMove={(event) => {
          const start = drag.current;
          if (!start || start.pointerId !== event.pointerId) return;
          const dx = event.clientX - start.client[0];
          const dy = event.clientY - start.client[1];
          if (Math.abs(dx) + Math.abs(dy) < 4 && !start.moved) return;
          start.moved = true;
          setCamera({
            width: start.camera.width,
            center: [
              start.camera.center[0] - dx / start.scale,
              start.camera.center[1] + dy / start.scale,
            ],
          });
        }}
        onPointerUp={(event) => {
          const start = drag.current;
          if (!start || start.pointerId !== event.pointerId) return;
          if (canPick && !start.moved) {
            const point = pointAt(
              event.clientX,
              event.clientY,
              event.currentTarget,
            );
            setCursor(point);
            onPoint?.(point);
          }
          finishDrag(event.currentTarget, event.pointerId);
        }}
        onPointerCancel={(event) => {
          if (drag.current?.pointerId === event.pointerId)
            finishDrag(event.currentTarget, event.pointerId);
        }}
        onLostPointerCapture={(event) => {
          if (drag.current?.pointerId === event.pointerId)
            finishDrag(event.currentTarget, event.pointerId);
        }}
        onKeyDown={(event) => {
          if (event.metaKey || event.ctrlKey) return;
          if (event.key === '+' || event.key === '=') zoom(1 / 1.35);
          else if (event.key === '-') zoom(1.35);
          else if (event.key === 'Home') fit();
          else if (event.key === 'Enter' || event.key === ' ') {
            if (canPick) onPoint?.(currentCursor);
          } else if (
            ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(
              event.key,
            )
          ) {
            const step = current.width / (event.altKey ? 300 : 50);
            const dx =
              event.key === 'ArrowLeft'
                ? -step
                : event.key === 'ArrowRight'
                  ? step
                  : 0;
            const dy =
              event.key === 'ArrowUp'
                ? step
                : event.key === 'ArrowDown'
                  ? -step
                  : 0;
            if (event.shiftKey || !canPick)
              setCamera({
                width: current.width,
                center: [current.center[0] + dx, current.center[1] + dy],
              });
            else setCursor([currentCursor[0] + dx, currentCursor[1] + dy]);
          } else return;
          event.preventDefault();
          event.stopPropagation();
        }}
      >
        {streets.map((line, index) => (
          <path
            key={`street-${index}`}
            d={path(line)}
            fill="none"
            stroke="var(--color-blue-300)"
            strokeWidth="2"
            strokeLinecap="round"
          />
        ))}
        {lines.map((line, index) => (
          <path
            key={`base-${index}`}
            d={path(line)}
            fill="none"
            stroke="var(--color-neutral-500)"
            strokeWidth="1"
          />
        ))}
        {overlay.map((line, index) => (
          <path
            key={`overlay-${index}`}
            d={path(line)}
            fill="none"
            stroke="var(--color-blue-600)"
            strokeWidth="1.5"
            strokeDasharray="5 3"
          />
        ))}
        {boundary.map((line, index) => (
          <path
            key={`boundary-${index}`}
            d={path(line)}
            fill="none"
            className="stroke-amber-600"
            strokeWidth="1.5"
            strokeDasharray="7 4"
          />
        ))}
        {markers.map(({ point, number, holdout }) => {
          const selected =
            activePair !== undefined && activePair + 1 === number;
          const color = selected
            ? 'var(--color-blue-600)'
            : 'var(--color-neutral-500)';
          const [x, y] = screenPoint(point);
          return (
            <g
              key={number}
              data-marker={number}
              data-active={selected || undefined}
              transform={`translate(${x},${y})`}
            >
              {selected && <circle r="16" fill="var(--color-blue-100)" />}
              <circle
                r={
                  activePair !== undefined && activePair + 1 === number
                    ? 12
                    : 10
                }
                fill={holdout ? 'var(--color-white)' : color}
                stroke={color}
                strokeWidth="2"
              />
              <text
                textAnchor="middle"
                dominantBaseline="central"
                fontSize="11"
                fontWeight="600"
                fill={holdout ? color : 'var(--color-white)'}
              >
                {number}
              </text>
            </g>
          );
        })}
        {canPick && cursor && (
          <g
            transform={`translate(${cursorScreen[0]},${cursorScreen[1]})`}
            stroke="var(--color-blue-700)"
            strokeWidth="1.25"
            pointerEvents="none"
          >
            <path d="M -8 0 H 8 M 0 -8 V 8" />
            <circle r="3" fill="var(--color-white)" />
          </g>
        )}
        {!lines.length &&
          !streets.length &&
          !overlay.length &&
          !boundary.length && (
            <text
              x={size.width / 2}
              y={size.height / 2}
              textAnchor="middle"
              fill="var(--color-neutral-500)"
              fontSize="13"
            >
              Нет контуров для сопоставления
            </text>
          )}
      </svg>
      <div className="top-canvas-inset right-canvas-inset pointer-events-none absolute z-10 [&>*]:pointer-events-auto">
        <CanvasControlGroup
          orientation="vertical"
          label={`Навигация: ${title}`}
        >
          <IconButton
            icon={Plus}
            label={`Увеличить: ${title}`}
            variant="ghost"
            onClick={() => zoom(1 / 1.35)}
          />
          <IconButton
            icon={Minus}
            label={`Уменьшить: ${title}`}
            variant="ghost"
            onClick={() => zoom(1.35)}
          />
          <IconButton
            icon={Maximize}
            label={`Показать целиком: ${title}`}
            variant="ghost"
            onClick={fit}
          />
        </CanvasControlGroup>
      </div>
    </section>
  );
}
