import { localLayerName } from '@/entities/source-data/model/boundaryDecision';
import { LAYER_KIND_LABELS } from '@/entities/source-data/model/layerKinds';
import { LayerSymbol } from '@/entities/source-data/ui/LayerSymbol';
import type { Layer } from '@green/api-client';
import {
  Button,
  ControlProvider,
  Disclosure,
  Icon,
  IconButton,
  ScrollArea,
  Text,
  TextInput,
  Tooltip,
  cx,
} from '@green/ui';
import { Eye, EyeOff, Search, X } from 'lucide-react';
import { useMemo, useRef, useState, type FC } from 'react';
import {
  filterProjectLayers,
  projectLayerGroups,
} from '../model/explorerModel';

export interface ProjectLayersProps {
  layers: Layer[];
  visibility: Record<string, boolean>;
  activeLayerId?: string;
  onVisibility: (layerId: string, visible: boolean) => void;
  onGroupVisibility: (layerIds: string[], visible: boolean) => void;
  onSelect: (layerId: string) => void;
}

type LayerRowProps = Pick<
  ProjectLayersProps,
  'visibility' | 'activeLayerId' | 'onVisibility' | 'onSelect'
> & { layer: Layer };

/** Selection and visibility are independent actions within one layer surface. */
const LayerRow: FC<LayerRowProps> = ({
  layer,
  visibility,
  activeLayerId,
  onVisibility,
  onSelect,
}) => {
  const selectionRef = useRef<HTMLButtonElement>(null);
  const visible = visibility[layer.id] !== false;
  const localName = localLayerName(layer);
  const primary = layer.mapped_kind
    ? LAYER_KIND_LABELS[layer.mapped_kind]
    : localName;
  const secondary =
    layer.mapped_kind && localName !== primary ? localName : undefined;
  const selected = activeLayerId === layer.id;
  const selection = (
    <Button
      ref={selectionRef}
      variant="ghost"
      controlSize="compact"
      className="h-12 min-h-12 w-full min-w-0 justify-start px-2 py-0 text-left hover:bg-transparent"
      aria-pressed={selected}
      aria-label={`${primary}, исходный слой ${layer.source_name}`}
      startIcon={<LayerSymbol color={layer.color} kind={layer.mapped_kind} />}
      onClick={() => onSelect(layer.id)}
      content={
        <span className="grid min-w-0 flex-1">
          <Text variant="label" className="truncate leading-4">
            {primary}
          </Text>
          {secondary !== undefined && (
            <Text variant="caption" className="truncate leading-4">
              {secondary}
            </Text>
          )}
        </span>
      }
    />
  );
  return (
    <div
      className={cx(
        'rounded-control grid h-12 min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-0 pr-2 hover:bg-neutral-100',
        selected && 'bg-blue-100 hover:bg-blue-100',
      )}
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          selectionRef.current?.focus();
          onSelect(layer.id);
        }
      }}
    >
      <Tooltip content={layer.source_name} render={selection} />
      <IconButton
        icon={visible ? <Eye /> : <EyeOff />}
        label={visible ? 'Скрыть слой' : 'Показать слой'}
        className="hover:bg-neutral-200"
        variant="ghost"
        controlSize="compact"
        onClick={() => onVisibility(layer.id, !visible)}
      />
    </div>
  );
};

export const ProjectLayers: FC<ProjectLayersProps> = ({
  layers,
  visibility,
  activeLayerId,
  onVisibility,
  onGroupVisibility,
  onSelect,
}) => {
  const [query, setQuery] = useState('');
  const [groupOpen, setGroupOpen] = useState<Record<string, boolean>>({});
  const [searchGroupOpen, setSearchGroupOpen] = useState<
    Record<string, boolean>
  >({});
  const searchRef = useRef<HTMLInputElement>(null);
  const queryKey = query.trim().toLocaleLowerCase('ru');
  const visibleLayers = useMemo(
    () => filterProjectLayers(layers, query),
    [layers, query],
  );
  const groups = useMemo(() => projectLayerGroups(layers), [layers]);
  const matchedGroups = groups.filter(
    (group) => filterProjectLayers(group.layers, query).length > 0,
  );
  const changeQuery = (next: string) => {
    if (next.trim().toLocaleLowerCase('ru') !== queryKey)
      setSearchGroupOpen({});
    setQuery(next);
  };
  return (
    <ControlProvider size="compact">
      <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
        <div className="relative h-10 shrink-0 border-b border-neutral-200 after:pointer-events-none after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-(--focus) after:opacity-0 focus-within:after:opacity-100 hover:bg-neutral-100">
          <Icon
            icon={<Search />}
            className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2"
          />
          <TextInput
            ref={searchRef}
            variant="ghost"
            controlSize="compact"
            className="h-full border-0 pr-12 pl-10 focus-visible:border-0"
            aria-label="Найти слой"
            placeholder="Найти слой"
            value={query}
            onChange={(event) => changeQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape' && query) {
                event.preventDefault();
                event.stopPropagation();
                changeQuery('');
              }
            }}
          />
          {!!query && (
            <IconButton
              icon={<X />}
              label="Очистить поиск"
              className="absolute top-1/2 right-2 -translate-y-1/2"
              variant="ghost"
              controlSize="compact"
              onClick={() => {
                changeQuery('');
                searchRef.current?.focus();
              }}
            />
          )}
        </div>
        <ScrollArea
          className="flex-1"
          contentClassName="grid gap-1 px-2 pt-2 pb-6"
        >
          {!layers.length && (
            <Text variant="caption" className="px-2">
              Слоёв пока нет
            </Text>
          )}
          {!!layers.length && !visibleLayers.length && (
            <Text variant="caption" className="px-2">
              Совпадений нет
            </Text>
          )}
          {matchedGroups.map((group) => {
            const visibleCount = group.layers.filter(
              (layer) => visibility[layer.id] !== false,
            ).length;
            const visible = visibleCount > 0;
            const mixed = visible && visibleCount < group.layers.length;
            const countLabel = `Видно ${visibleCount} из ${group.layers.length} слоёв`;
            const scope = queryKey ? ' (включая слои вне поиска)' : '';
            const actionLabel = `${visible ? 'Скрыть' : 'Показать'} всю группу: ${group.label}${scope}`;
            const filtered = filterProjectLayers(group.layers, query);
            const open = queryKey
              ? (searchGroupOpen[group.id] ?? true)
              : (groupOpen[group.id] ?? true);
            return (
              <Disclosure
                key={group.id}
                label={group.label}
                variant="plain"
                headerVariant="trailing-actions"
                stickyHeader
                open={open}
                contentClassName="grid gap-1 pt-1 pb-0"
                title={
                  <span className="flex min-w-0 items-baseline gap-2">
                    <Text variant="label" className="truncate">
                      {group.label}
                    </Text>
                    <Text
                      variant="caption"
                      mono
                      className="shrink-0"
                      title={countLabel}
                      aria-label={countLabel}
                    >
                      {visibleCount}/{group.layers.length}
                    </Text>
                  </span>
                }
                actions={
                  <IconButton
                    icon={visible ? <Eye /> : <EyeOff />}
                    label={actionLabel}
                    variant="ghost"
                    controlSize="compact"
                    aria-pressed={mixed ? 'mixed' : visible}
                    title={
                      mixed
                        ? `Группа видна частично. ${actionLabel}`
                        : actionLabel
                    }
                    onClick={() =>
                      onGroupVisibility(
                        group.layers.map((layer) => layer.id),
                        !visible,
                      )
                    }
                  />
                }
                onOpenChange={(expanded) => {
                  const setOpen = queryKey ? setSearchGroupOpen : setGroupOpen;
                  setOpen((current) => ({
                    ...current,
                    [group.id]: expanded,
                  }));
                }}
              >
                {filtered.map((layer) => (
                  <LayerRow
                    key={layer.id}
                    layer={layer}
                    visibility={visibility}
                    activeLayerId={activeLayerId}
                    onVisibility={onVisibility}
                    onSelect={onSelect}
                  />
                ))}
              </Disclosure>
            );
          })}
        </ScrollArea>
      </div>
    </ControlProvider>
  );
};
