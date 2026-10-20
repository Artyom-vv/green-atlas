import type { PlanObject } from '@green/api-client';
import { Button, Disclosure, Text } from '@green/ui';
import { Sprout } from 'lucide-react';
import { useMemo, type FC } from 'react';
import { explorerPlantings } from '../model/explorerModel';

export interface ExplorerPlantingsProps {
  objects: PlanObject[];
  speciesNames: ReadonlyMap<string, string>;
  selectedIds: string[];
  disabled: boolean;
  onSelect: (ids: string[]) => void;
  onManage?: () => void;
}
export const ExplorerPlantings: FC<ExplorerPlantingsProps> = ({
  objects,
  speciesNames,
  selectedIds,
  disabled,
  onSelect,
  onManage,
}) => {
  const { groups, visibleCount } = useMemo(
    () => explorerPlantings(objects, speciesNames),
    [objects, speciesNames],
  );
  const selected = new Set(selectedIds);
  return (
    <Disclosure
      variant="plain"
      headerVariant="inset"
      stickyHeader
      defaultOpen
      label="Посадки проекта"
      contentClassName="py-1"
      title={
        <span className="inline-flex items-center gap-2">
          Посадки{' '}
          <Text variant="caption" mono>
            {objects.length}
          </Text>
        </span>
      }
    >
      {!!groups.length && (
        <div className="grid gap-3">
          {groups.map((group) => (
            <section
              key={group.kind}
              aria-label={group.label}
              className="grid min-w-0 gap-1"
            >
              <Text
                as="h3"
                variant="label"
                tone="muted"
                className="py-1 pr-2 pl-8"
              >
                {group.label}
              </Text>
              <ul className="m-0 grid list-none gap-1 p-0">
                {group.items.map(({ object, ordinal, name }) => (
                  <li key={object.id ?? `planting-${ordinal}`}>
                    <Button
                      variant="ghost"
                      className="w-full justify-start px-2 text-left aria-pressed:bg-blue-100 aria-pressed:text-blue-700"
                      aria-label={`Выбрать посадку № ${ordinal}: ${name}`}
                      title={name}
                      aria-pressed={Boolean(
                        object.id && selected.has(object.id),
                      )}
                      disabled={disabled || !object.id}
                      onClick={() => {
                        if (object.id) onSelect([object.id]);
                      }}
                      content={
                        <span className="grid min-w-0 flex-1 grid-cols-[1rem_minmax(0,1fr)] items-baseline gap-2">
                          <Text variant="caption" mono className="w-4">
                            {ordinal}
                          </Text>
                          <span className="truncate">{name}</span>
                        </span>
                      }
                    />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
      {objects.length > visibleCount && (
        <Text variant="caption" className="px-2">
          Показано {visibleCount} из {objects.length} посадок
        </Text>
      )}
      {!!onManage && (
        <Button
          variant="ghost"
          icon={<Sprout />}
          className="w-full justify-start px-2"
          disabled={disabled}
          onClick={onManage}
        >
          Все посадки
        </Button>
      )}
    </Disclosure>
  );
};
