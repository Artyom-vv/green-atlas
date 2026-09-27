import { LayerSymbol } from '@/entities/source-data/ui/LayerSymbol';
import type { Layer } from '@green/api-client';
import {
  Button,
  ControlProvider,
  IconButton,
  ScrollArea,
  Text,
  TextInput,
  cx,
} from '@green/ui';
import { Eye, EyeOff, Search } from 'lucide-react';
import { useMemo, useState, type FC } from 'react';
import {
  filterProjectLayers,
  layerDisplayLabel,
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
export const ProjectLayers: FC<ProjectLayersProps> = ({
  layers,
  visibility,
  activeLayerId,
  onVisibility,
  onGroupVisibility,
  onSelect,
}) => {
  const [query, setQuery] = useState('');
  const visibleLayers = useMemo(
    () => filterProjectLayers(layers, query),
    [layers, query],
  );
  const groups = useMemo(() => projectLayerGroups(layers), [layers]);
  return (
    <ControlProvider size="compact">
      <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
        <div className="grid shrink-0 gap-1 border-b border-gray-200 p-2">
          {groups.map((group) => {
            const visibleCount = group.layers.filter(
              (layer) => visibility[layer.id] !== false,
            ).length;
            const visible = visibleCount > 0;
            return (
              <div
                key={group.id}
                className="rounded-control grid grid-cols-[minmax(0,1fr)_auto] items-center gap-1"
              >
                <div className="grid min-w-0 px-2 py-1">
                  <Text variant="label">{group.label}</Text>
                  <Text variant="caption">
                    {visibleCount} из {group.layers.length}
                  </Text>
                </div>
                <IconButton
                  icon={visible ? <Eye /> : <EyeOff />}
                  label={
                    visible
                      ? `Скрыть: ${group.label}`
                      : `Показать: ${group.label}`
                  }
                  variant="ghost"
                  onClick={() =>
                    onGroupVisibility(
                      group.layers.map((layer) => layer.id),
                      !visible,
                    )
                  }
                />
              </div>
            );
          })}
          <TextInput
            startIcon={<Search />}
            aria-label="Найти слой"
            placeholder="Найти слой"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <ScrollArea className="flex-1" contentClassName="grid gap-1 p-2">
          {visibleLayers.map((layer) => {
            const visible = visibility[layer.id] !== false;
            return (
              <div
                key={layer.id}
                className={cx(
                  'rounded-control grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-1',
                  activeLayerId === layer.id && 'bg-blue-100',
                )}
              >
                <Button
                  variant="ghost"
                  className="h-auto min-w-0 justify-start py-2 text-left"
                  aria-pressed={activeLayerId === layer.id}
                  title={layer.source_name}
                  startIcon={
                    <LayerSymbol color={layer.color} kind={layer.mapped_kind} />
                  }
                  onClick={() => onSelect(layer.id)}
                  content={
                    <span className="grid min-w-0 flex-1 gap-1">
                      <Text variant="label" className="wrap-anywhere">
                        {layerDisplayLabel(layer)}
                      </Text>
                      <Text variant="caption" className="break-all">
                        {layer.source_name}
                      </Text>
                    </span>
                  }
                />
                <IconButton
                  icon={visible ? <Eye /> : <EyeOff />}
                  label={visible ? 'Скрыть слой' : 'Показать слой'}
                  variant="ghost"
                  onClick={() => onVisibility(layer.id, !visible)}
                />
              </div>
            );
          })}
          {!visibleLayers.length && (
            <Text variant="caption">Слои не найдены</Text>
          )}
        </ScrollArea>
      </div>
    </ControlProvider>
  );
};
