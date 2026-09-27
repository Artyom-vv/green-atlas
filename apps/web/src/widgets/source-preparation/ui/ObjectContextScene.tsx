import { useRef, useState } from 'react';
import type {
  SourceContextObject,
  SourceObjectContext,
} from '@green/api-client';
import { IconButton } from '@green/ui';
import { Maximize, Minus, Plus } from 'lucide-react';

const COLORS: Record<string, string> = {
  building: '#66758a',
  road: '#8b939d',
  utility: '#4279b8',
  existing_green: '#4b9276',
  lawn: '#77ac85',
  restricted: '#997549',
};

export function ObjectContextScene({
  data,
  active,
  chosen,
  assembling,
  onPick,
  onInspect,
  overlay,
}: {
  data: SourceObjectContext;
  active?: string;
  chosen: Set<string>;
  assembling: boolean;
  onPick: (item: SourceContextObject) => void;
  onInspect: (item: SourceContextObject) => void;
  overlay?: { path: number[][]; repairs: number[][][] };
}) {
  const [view, setView] = useState({ zoom: 1, x: 0, y: 0 });
  const [hover, setHover] = useState<SourceContextObject>();
  const drag = useRef<
    { x: number; y: number; vx: number; vy: number; moved: boolean } | undefined
  >(undefined);
  const extent = [...data.extent];
  for (const [x, y] of overlay?.path ?? []) {
    extent[0] = Math.min(extent[0], x - 2);
    extent[1] = Math.min(extent[1], y - 2);
    extent[2] = Math.max(extent[2], x + 2);
    extent[3] = Math.max(extent[3], y + 2);
  }
  const [x0, y0, x1, y1] = extent;
  const width = x1 - x0,
    height = y1 - y0;
  const zoom = (factor: number) =>
    setView((v) => ({
      ...v,
      zoom: Math.max(0.4, Math.min(12, v.zoom * factor)),
    }));
  const visibleWidth = width / view.zoom,
    visibleHeight = height / view.zoom;
  const sx = (width - visibleWidth) / 2 + view.x,
    sy = (height - visibleHeight) / 2 + view.y;
  const path = (points: number[][]) =>
    points.map(([x, y], i) => `${i ? 'L' : 'M'}${x - x0} ${y1 - y}`).join(' ');
  // Filled areas must not intercept picking of their visible line neighbors.
  const priority = (item: SourceContextObject) =>
    chosen.has(item.route)
      ? 3
      : item.route === active
        ? 2
        : item.native_area
          ? 0
          : 1;
  const objects = [...data.objects].sort((a, b) => priority(a) - priority(b));
  return (
    <div className="relative h-full min-h-0 overflow-hidden bg-[#f6f8fa]">
      <svg
        role="group"
        tabIndex={0}
        aria-label="Выбранный объект и окружение чертежа"
        className="h-full w-full touch-none focus-visible:outline-2 focus-visible:outline-blue-500"
        onKeyDown={(event) => {
          if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
            event.preventDefault();
            event.stopPropagation();
            const index = data.objects.findIndex(
              (item) => item.route === active,
            );
            const next =
              data.objects[
                Math.max(
                  0,
                  Math.min(
                    data.objects.length - 1,
                    index + (event.key === 'ArrowRight' ? 1 : -1),
                  ),
                )
              ];
            if (next) onInspect(next);
          } else if (event.key === 'Enter' && assembling) {
            event.preventDefault();
            event.stopPropagation();
            const item = data.objects.find((item) => item.route === active);
            if (item) onPick(item);
          } else if (event.key === '+' || event.key === '-') {
            event.preventDefault();
            zoom(event.key === '+' ? 1.4 : 1 / 1.4);
          }
        }}
        viewBox={`${sx} ${sy} ${visibleWidth} ${visibleHeight}`}
        preserveAspectRatio="xMidYMid meet"
        onWheel={(event) => {
          event.preventDefault();
          zoom(event.deltaY < 0 ? 1.2 : 1 / 1.2);
        }}
        onPointerDown={(event) => {
          drag.current = {
            x: event.clientX,
            y: event.clientY,
            vx: view.x,
            vy: view.y,
            moved: false,
          };
        }}
        onPointerMove={(event) => {
          const current = drag.current;
          if (current && event.buttons) {
            const box = event.currentTarget.getBoundingClientRect();
            const ratio = Math.max(
              visibleWidth / box.width,
              visibleHeight / box.height,
            );
            const dx = event.clientX - current.x,
              dy = event.clientY - current.y;
            if (Math.hypot(dx, dy) > 3) {
              current.moved = true;
              event.currentTarget.setPointerCapture(event.pointerId);
            }
            if (current.moved)
              setView((v) => ({
                ...v,
                x: current.vx - dx * ratio,
                y: current.vy - dy * ratio,
              }));
          }
        }}
        onPointerUp={(event) => {
          if (drag.current?.moved) event.preventDefault();
          if (event.currentTarget.hasPointerCapture(event.pointerId))
            event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={() => {
          drag.current = undefined;
        }}
        onClick={(event) => {
          if (drag.current?.moved) {
            event.stopPropagation();
          }
          drag.current = undefined;
        }}
      >
        {overlay && (
          <g pointerEvents="none">
            <path
              d={path(overlay.path)}
              fill="#21956b"
              fillOpacity={0.18}
              stroke="#21956b"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
            {overlay.repairs.map((repair, index) => (
              <path
                key={index}
                d={path(repair)}
                fill="none"
                stroke="#b65c0c"
                strokeWidth={5}
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </g>
        )}
        {objects.map((item) => {
          const selected = chosen.has(item.route),
            focused = item.route === active;
          const color = selected
            ? '#b65c0c'
            : focused
              ? '#315bdf'
              : (COLORS[item.kind] ?? '#a2a8b0');
          return (
            <g
              key={item.route}
              data-route={item.route}
              onMouseEnter={() => setHover(item)}
              onMouseLeave={() => setHover(undefined)}
            >
              <path
                d={item.paths.map(path).join(' ')}
                fill={item.native_area ? color : 'none'}
                fillOpacity={0.09}
                fillRule="evenodd"
                stroke={color}
                strokeWidth={selected ? 3 : focused ? 2.5 : 1}
                vectorEffect="non-scaling-stroke"
              />
              <path
                d={item.paths.map(path).join(' ')}
                fill={item.native_area ? 'transparent' : 'none'}
                stroke="transparent"
                strokeWidth={9}
                vectorEffect="non-scaling-stroke"
                className={
                  assembling && item.can_join
                    ? 'cursor-crosshair'
                    : 'cursor-pointer'
                }
                onClick={(event) => {
                  event.stopPropagation();
                  if (drag.current?.moved) {
                    drag.current = undefined;
                    return;
                  }
                  drag.current = undefined;
                  if (assembling) onPick(item);
                  else onInspect(item);
                }}
              >
                <title>{`${item.layer} — ${item.source.handle}`}</title>
              </path>
              {focused &&
                !item.native_area &&
                item.paths[0]?.length > 1 &&
                [item.paths[0][0], item.paths[0].at(-1)!].map(
                  ([x, y], index) => (
                    <circle
                      key={index}
                      cx={x - x0}
                      cy={y1 - y}
                      r={Math.max(visibleWidth, visibleHeight) / 150}
                      fill="white"
                      stroke="#b65c0c"
                      strokeWidth={2}
                      vectorEffect="non-scaling-stroke"
                      pointerEvents="none"
                    />
                  ),
                )}
            </g>
          );
        })}
      </svg>
      <div className="absolute top-2 right-2 flex rounded border border-neutral-200 bg-white shadow-sm">
        <IconButton
          icon={Plus}
          label="Приблизить окружение"
          onClick={() => zoom(1.4)}
        />
        <IconButton
          icon={Minus}
          label="Отдалить окружение"
          onClick={() => zoom(1 / 1.4)}
        />
        <IconButton
          icon={Maximize}
          label="Показать окружение целиком"
          onClick={() => setView({ zoom: 1, x: 0, y: 0 })}
        />
      </div>
      <div className="pointer-events-none absolute right-2 bottom-2 left-2 flex justify-between gap-3 text-xs text-neutral-600">
        <span className="max-w-[75%] truncate rounded bg-white/95 px-2 py-1">
          {hover
            ? `${hover.layer} — ${hover.source.handle}`
            : 'Колесо — масштаб, перетаскивание — перемещение'}
        </span>
        {data.limited && (
          <span className="rounded bg-white/95 px-2 py-1">
            Показано {data.objects.length} из {data.total}
          </span>
        )}
      </div>
    </div>
  );
}
