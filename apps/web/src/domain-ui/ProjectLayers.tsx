import { useMemo, useState, type CSSProperties } from 'react';
import type { Layer } from '@green/api-client';
import { Eye, EyeOff, MapPinned, PanelLeftClose, Search } from 'lucide-react';
import { IconButton } from '@green/ui';

const kindLabels: Record<string, string> = { site_border: 'Границы участка', building: 'Здания', road: 'Дороги и проезды', utility: 'Инженерные сети', existing_green: 'Существующее озеленение', water: 'Водные объекты', restricted: 'Технические зоны', ignore: 'Не используется' };

export function ProjectLayers({ layers, visibility, activeLayerId, onVisibility, onSelect, onZones, onClose }: { layers: Layer[]; visibility: Record<string, boolean>; activeLayerId?: string; onVisibility: (layerId: string, visible: boolean) => void; onSelect: (layerId: string) => void; onZones?: () => void; onClose?: () => void }) {
  const [query, setQuery] = useState('');
  const visibleLayers = useMemo(() => {
    const value = query.trim().toLocaleLowerCase('ru');
    if (!value) return layers;
    return layers.filter((layer) => `${kindLabels[layer.mapped_kind ?? ''] ?? ''} ${layer.source_name}`.toLocaleLowerCase('ru').includes(value));
  }, [layers, query]);
  return (
    <div className="layers-rail">
      <header><strong>Слои</strong><span>{onZones ? <IconButton icon={MapPinned} label="Рабочие участки" variant="ghost" onClick={onZones} /> : null}{onClose ? <IconButton icon={PanelLeftClose} label="Свернуть слои" variant="ghost" onClick={onClose} /> : null}</span></header>
      <label className="layer-search"><Search size={16} /><input aria-label="Найти слой" placeholder="Найти слой" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <div className="layer-list">
        {visibleLayers.map((layer) => {
          const visible = visibility[layer.id] !== false;
          const label = layer.mapped_kind ? kindLabels[layer.mapped_kind] : layer.source_name;
          return <div className={`layer-row ${activeLayerId === layer.id ? 'is-selected' : ''}`} key={layer.id}>
            <button className="layer-row__select" type="button" onClick={() => onSelect(layer.id)}>
              <span className={`layer-symbol layer-symbol--${layer.mapped_kind ?? 'default'}`} style={{ '--layer-color': layer.color } as CSSProperties} />
              <span className="layer-row__body"><strong>{label}</strong><small>{layer.source_name}</small></span>
            </button>
            <IconButton icon={visible ? Eye : EyeOff} label={visible ? 'Скрыть слой' : 'Показать слой'} variant="ghost" onClick={() => onVisibility(layer.id, !visible)} />
          </div>;
        })}
        {!visibleLayers.length ? <div className="layer-list__empty">Слои не найдены</div> : null}
      </div>
      <div className="layers-rail__spacer" />
    </div>
  );
}
