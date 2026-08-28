import type { Layer } from '@green/api-client';
import { Eye, EyeOff, LocateFixed } from 'lucide-react';
import { Button } from '@green/ui';

const kindLabels: Record<string, string> = {
  site_border: 'Граница участка',
  building: 'Здания и сооружения',
  road: 'Дороги и проезды',
  utility: 'Инженерные сети',
  existing_green: 'Существующее озеленение',
  ignore: 'Справочная геометрия',
};

export function LayerInspector({ layer, visible, onVisibility, onFit }: { layer: Layer; visible: boolean; onVisibility: (visible: boolean) => void; onFit: () => void }) {
  const role = kindLabels[layer.mapped_kind ?? ''] ?? 'Справочная геометрия';
  return (
    <div className="layer-inspector">
      <header><span><strong>{kindLabels[layer.mapped_kind ?? ''] ?? layer.source_name}</strong><small>{layer.source_name}</small></span></header>
      <section>
        <h3>{role}</h3>
        <p className="layer-inspector__description" aria-live="polite">{visible ? `${layer.object_count} объектов на исходном чертеже.` : 'Слой скрыт'}</p>
        <div className="layer-inspector__actions"><Button variant="secondary" icon={visible ? EyeOff : Eye} onClick={() => onVisibility(!visible)}>{visible ? 'Скрыть слой' : 'Показать слой'}</Button><Button variant="secondary" icon={LocateFixed} disabled={!visible} onClick={onFit}>Найти на карте</Button></div>
      </section>
    </div>
  );
}
