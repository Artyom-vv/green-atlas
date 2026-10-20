import { useState } from 'react';
import type { LocationBounds, LocationPoint } from '@green/api-client';
import {
  Button,
  IconButton,
  Popover,
  PopoverTrigger,
  PopoverPopup,
} from '@green/ui';
import { Check, List, Minus, Pencil } from 'lucide-react';
import { LocationPlan } from './LocationPlan';

export type Pair = {
  cad?: LocationPoint;
  map?: LocationPoint;
  holdout: boolean;
};

export function PairPicker({
  pairs,
  active,
  onSelect,
}: {
  pairs: Pair[];
  active: number;
  onSelect: (index: number) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={<Button variant="ghost" startIcon={<List />} />}>
        Изменить точки
      </PopoverTrigger>
      <PopoverPopup
        side="top"
        align="start"
        className="w-72 overflow-hidden p-0"
      >
        <div className="grid grid-cols-[1fr_56px_56px] gap-2 border-b border-neutral-200 px-3 py-2 text-xs text-neutral-600">
          <span>Пара</span>
          <span className="text-center">Чертёж</span>
          <span className="text-center">Карта</span>
        </div>
        {pairs.map((p, i) => (
          <Button
            key={i}
            variant="ghost"
            aria-label={`Редактировать пару ${i + 1}`}
            aria-pressed={active === i}
            className="w-full rounded-none px-3 py-2 text-left aria-pressed:bg-neutral-100"
            onClick={() => {
              onSelect(i);
              setOpen(false);
            }}
            content={
              <span className="grid w-full grid-cols-[1fr_56px_56px] items-center gap-2">
                <span>{i === 3 ? 'Контроль' : i + 1}</span>
                <span className="flex justify-center">
                  {p.cad ? (
                    <Check className="size-4" />
                  ) : (
                    <Minus className="size-4 text-neutral-400" />
                  )}
                </span>
                <span className="flex justify-center">
                  {p.map ? (
                    <Check className="size-4" />
                  ) : (
                    <Minus className="size-4 text-neutral-400" />
                  )}
                </span>
              </span>
            }
          />
        ))}
      </PopoverPopup>
    </Popover>
  );
}

export function LocationManualWorkspace({
  pairs,
  active,
  cad,
  osm,
  streets,
  cadBounds,
  mapBounds,
  onPlace,
  onEdit,
  busy = false,
}: {
  pairs: Pair[];
  active: number;
  cad: LocationPoint[][];
  osm: LocationPoint[][];
  streets: LocationPoint[][];
  cadBounds?: LocationBounds;
  mapBounds?: LocationBounds;
  onPlace: (side: 'cad' | 'map', point: LocationPoint) => void;
  onEdit: (side: 'cad' | 'map') => void;
  busy?: boolean;
}) {
  const pair = pairs[active];
  const target = !pair.cad ? 'cad' : !pair.map ? 'map' : null;
  const markers = (side: 'cad' | 'map') =>
    pairs.flatMap((p, i) =>
      p[side] ? [{ point: p[side]!, number: i + 1, holdout: p.holdout }] : [],
    );
  return (
    <>
      <div className="grid min-h-0 flex-1 grid-cols-2 divide-x divide-neutral-200">
        {(['cad', 'map'] as const).map((side, index) => (
          <div key={side} className="relative flex min-h-0 flex-col">
            <div className="flex min-h-12 shrink-0 items-center justify-between gap-2 border-b border-neutral-200 px-4 py-2">
              <div className="flex min-w-0 items-center gap-2">
                <span
                  className={`flex size-6 shrink-0 items-center justify-center rounded-full border text-xs ${target === side ? 'border-blue-300 bg-blue-50 text-blue-700' : 'border-neutral-300 text-neutral-600'}`}
                >
                  {pair[side] ? <Check className="size-3.5" /> : index + 1}
                </span>
                <span className="truncate text-sm">
                  {side === 'cad' ? 'Угол на чертеже' : 'Тот же угол на карте'}
                </span>
              </div>
              {pair[side] && (
                <IconButton
                  icon={Pencil}
                  label={
                    side === 'cad'
                      ? 'Изменить точку на чертеже'
                      : 'Изменить точку на карте'
                  }
                  variant="ghost"
                  controlSize="compact"
                  disabled={busy}
                  onClick={() => onEdit(side)}
                />
              )}
            </div>
            <LocationPlan
              title={side === 'cad' ? 'CAD' : 'OSM'}
              lines={side === 'cad' ? cad : osm}
              streets={side === 'map' ? streets : []}
              fitBounds={side === 'cad' ? cadBounds : mapBounds}
              activePair={active}
              markers={markers(side)}
              disabled={busy || target !== side}
              onPoint={(p) => onPlace(side, p)}
            />
            {side === 'map' && (
              <a
                href="https://www.openstreetmap.org/copyright"
                target="_blank"
                rel="noreferrer"
                className="absolute right-4 bottom-2 bg-white/90 px-1 text-xs text-neutral-600 underline"
              >
                © OpenStreetMap contributors
              </a>
            )}
          </div>
        ))}
      </div>
      <div role="status" aria-live="polite" className="sr-only">
        {target === 'cad'
          ? 'Выберите угол на чертеже'
          : target === 'map'
            ? 'Выберите тот же угол на карте'
            : 'Пара сопоставлена'}
      </div>
    </>
  );
}
