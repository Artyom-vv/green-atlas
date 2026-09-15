import type { FC } from 'react';
import type { SpeciesRevision } from '@green/api-client';
import { Button, cx } from '@green/ui';
import { Check } from 'lucide-react';
import { SpeciesPhoto } from './SpeciesPhoto';
interface SpeciesCatalogItemProps {
  item: SpeciesRevision;
  index: number;
  descriptionId: string;
  status?: { label: string; tone: 'neutral' | 'warning' };
  chosen: boolean;
  disabled: boolean;
  compact: boolean;
  onChange: (id: string) => void;
}
export const SpeciesCatalogItem: FC<SpeciesCatalogItemProps> = ({
  item,
  index,
  descriptionId,
  status,
  chosen,
  disabled,
  compact,
  onChange,
}) => (
  <article
    key={item.id}
    className={cx(
      'relative min-w-0 gap-2 rounded-md border border-solid p-2.5 has-[button:focus-visible]:outline-2 has-[button:focus-visible]:outline-blue-600',
      compact
        ? 'grid grid-cols-[64px_minmax(0,1fr)_20px] gap-x-2.5 gap-y-1.5'
        : 'flex flex-col',
      chosen ? 'border-blue-600 bg-blue-100' : 'border-neutral-300 bg-white',
      disabled && 'opacity-60',
    )}
  >
    <div className={compact ? 'col-start-1 row-span-3 row-start-1' : undefined}>
      <SpeciesPhoto species={item} size={compact ? 'compact' : 'catalog'} />
    </div>
    <div
      className={cx(
        'grid min-w-0 gap-0.5 wrap-anywhere',
        compact && 'col-start-2 row-start-1',
      )}
    >
      <strong className="text-sm leading-5 font-semibold">
        {item.common_name}
      </strong>
      <span
        className="text-xs leading-4 text-neutral-600"
        id={`${descriptionId}-name-${index}`}
      >
        {item.scientific_name}
      </span>
    </div>
    {status && (
      <span
        id={`${descriptionId}-status-${index}`}
        className={cx(
          'text-xs leading-4',
          compact && 'col-span-2 col-start-2',
          status.tone === 'warning' ? 'text-amber-700' : 'text-neutral-600',
        )}
      >
        {status.label}
      </span>
    )}
    <p
      id={`${descriptionId}-size-${index}`}
      className={cx(
        'm-0 flex flex-wrap gap-x-3 gap-y-0.5 text-xs leading-4 text-neutral-600 tabular-nums',
        compact && 'col-span-2 col-start-2',
      )}
    >
      <span>
        Высота {item.mature_height_min_m}–{item.mature_height_max_m} м
      </span>
      <span>
        Крона {item.mature_crown_diameter_min_m}–
        {item.mature_crown_diameter_max_m} м
      </span>
    </p>
    <Button
      variant="ghost"
      className="absolute inset-0 h-full w-full rounded-[inherit] border-0 bg-transparent p-0 hover:bg-blue-100/20"
      disabled={disabled}
      aria-label={
        chosen ? `Выбрано: ${item.common_name}` : `Выбрать: ${item.common_name}`
      }
      aria-describedby={[
        `${descriptionId}-name-${index}`,
        status
          ? `${descriptionId}-status-${index}`
          : `${descriptionId}-size-${index}`,
      ].join(' ')}
      aria-pressed={chosen}
      onClick={() => onChange(item.id)}
    >
      {chosen && (
        <Check
          className="absolute top-2.5 right-2.5 rounded-sm bg-white text-blue-700"
          size={18}
          aria-hidden="true"
        />
      )}
    </Button>
  </article>
);
