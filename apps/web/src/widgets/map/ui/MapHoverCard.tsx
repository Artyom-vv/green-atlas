import { Button, Text, tv } from '@green/ui';
import type { CSSProperties, FC } from 'react';
import type { MapHoverItem, MapHoverTarget } from '../model/mapContracts';

const hoverCard = tv({
  base: 'rounded-card absolute top-[clamp(8px,var(--map-hover-y),calc(100%-228px))] left-[clamp(8px,var(--map-hover-x),calc(100%-min(280px,calc(100%-16px))-8px))] z-20 flex max-h-55 w-max max-w-[min(280px,calc(100%-16px))] flex-col overflow-auto overscroll-contain border border-solid border-neutral-300 bg-white/95 text-neutral-800',
  variants: {
    interactive: {
      true: 'pointer-events-auto shadow-lg',
      false: 'pointer-events-none',
    },
  },
});

export interface MapHoverCardProps {
  target: MapHoverTarget;
  onSelect?: (item: MapHoverItem) => void;
}

export const MapHoverCard: FC<MapHoverCardProps> = ({ target, onSelect }) => (
  <div
    className={hoverCard({ interactive: Boolean(onSelect) })}
    style={
      {
        '--map-hover-x': `${target.pixel[0] + 14}px`,
        '--map-hover-y': `${target.pixel[1] + 14}px`,
      } as CSSProperties
    }
    role="status"
    aria-label={
      target.kind === 'preview'
        ? 'Проверка новой позиции'
        : onSelect
          ? 'Выбор объекта карты'
          : 'Информация об объекте карты'
    }
    onMouseDown={(event) => event.stopPropagation()}
  >
    {target.items.map((item) => {
      const content = (
        <span className="grid gap-1 text-left">
          <Text as="strong" variant="label">
            {item.label}
          </Text>
          <Text variant="caption">{item.detail}</Text>
        </span>
      );
      return onSelect ? (
        <Button
          key={item.id}
          variant="ghost"
          className="h-auto min-h-12 w-full justify-start rounded-none border-0 border-b border-solid border-neutral-200 px-3 py-2 last:border-b-0"
          aria-label={`${item.preview ? 'Показать проверку' : 'Выбрать'} ${item.label}`}
          onClick={(event) => {
            event.stopPropagation();
            onSelect(item);
          }}
          content={content}
        />
      ) : (
        <div
          key={item.id}
          className="border-0 border-b border-solid border-neutral-200 px-3 py-2 last:border-b-0"
        >
          {content}
        </div>
      );
    })}
  </div>
);
