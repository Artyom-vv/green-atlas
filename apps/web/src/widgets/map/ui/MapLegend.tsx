/** Обозначения видимых слоёв карты в стандартной раскрывающейся панели UI */
import type { Layer } from '@green/api-client';
import {
  Button,
  IconButton,
  Popover,
  PopoverPopup,
  PopoverTrigger,
  PopoverTitle,
} from '@green/ui';
import { ChevronDown, List } from 'lucide-react';
import {
  canvasControlClassName,
  canvasSurfaceClassName,
} from '@/shared/ui/CanvasControls';
import {
  MAP_DESIGN_LEGEND,
  MAP_DESIGN_PALETTE,
} from '../model/mapDesignPalette';

export function MapLegend({
  layers,
  embedded = false,
  disabled = false,
}: {
  layers: Layer[];
  embedded?: boolean;
  disabled?: boolean;
}) {
  const visible = new Set(
    layers
      .filter((layer) => layer.visible && layer.object_count > 0)
      .map((layer) => layer.mapped_kind),
  );
  const items = MAP_DESIGN_LEGEND.filter((item) => visible.has(item.kind));
  if (!items.length && !embedded) return null;
  if (embedded && (disabled || !items.length))
    return (
      <IconButton
        variant="ghost"
        controlSize="compact"
        icon={List}
        label="Легенда"
        disabled
        title={
          disabled
            ? 'Легенда доступна в проектном виде'
            : 'Нет видимых обозначений'
        }
      />
    );
  return (
    <Popover>
      <PopoverTrigger
        className="group"
        render={
          embedded ? (
            <IconButton
              variant="ghost"
              controlSize="compact"
              icon={List}
              label="Легенда"
            />
          ) : (
            <Button
              variant="secondary"
              className={embedded ? canvasControlClassName : undefined}
              controlSize="compact"
              endIcon={
                <ChevronDown className="group-data-[popup-open]:rotate-180" />
              }
            >
              Легенда
            </Button>
          )
        }
      />
      <PopoverPopup
        align="end"
        sideOffset={embedded ? 13 : 4}
        className={`${canvasSurfaceClassName} w-64 cursor-default text-xs [scrollbar-gutter:auto]`}
        aria-label="Обозначения проектного вида"
      >
        {embedded && (
          <PopoverTitle className="m-0 mb-4 text-sm font-medium">
            Легенда
          </PopoverTitle>
        )}
        <ul className="m-0 grid list-none gap-3 p-0">
          {items.map(({ kind, label, shape }) => {
            const [fill, stroke] = MAP_DESIGN_PALETTE[kind];
            return (
              <li key={kind} className="flex items-center gap-3">
                <span
                  aria-hidden="true"
                  className={
                    shape === 'area'
                      ? 'inline-block h-3.5 w-6 shrink-0 rounded-sm border border-solid'
                      : 'inline-block w-6 shrink-0 border-t-2 border-dashed'
                  }
                  style={
                    shape === 'area'
                      ? {
                          background: fill,
                          borderColor: stroke,
                        }
                      : { borderColor: stroke }
                  }
                />
                <span>{label}</span>
              </li>
            );
          })}
        </ul>
      </PopoverPopup>
    </Popover>
  );
}
