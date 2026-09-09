import type { Layer } from '@green/api-client';
import { Eye, EyeOff, LocateFixed } from 'lucide-react';
import { Button } from '@green/ui';
import { EditorActions, EditorPanel } from './EditorPanel';

const kindLabels: Record<string, string> = {
  site_border: 'Граница участка',
  building: 'Здания и сооружения',
  road: 'Дороги и проезды',
  utility: 'Инженерные сети',
  existing_green: 'Существующее озеленение',
  water: 'Водный объект',
  restricted: 'Техническая или непригодная зона',
  ignore: 'Справочная геометрия',
};

export function LayerInspector({ layer, visible, onVisibility, onFit }: { layer: Layer; visible: boolean; onVisibility: (visible: boolean) => void; onFit: () => void }) {
  const role = kindLabels[layer.mapped_kind ?? ''] ?? 'Справочная геометрия';
  return (
    <EditorPanel title={role}>
      <p>{layer.source_name}</p>
      <p className="editor-panel__hint" aria-live="polite">{visible ? `${layer.object_count} объектов на исходном чертеже.` : 'Слой скрыт'}</p>
      <EditorActions grid><Button variant="secondary" icon={visible ? EyeOff : Eye} onClick={() => onVisibility(!visible)}>{visible ? 'Скрыть слой' : 'Показать слой'}</Button><Button variant="secondary" icon={LocateFixed} disabled={!visible} onClick={onFit}>На карте</Button></EditorActions>
    </EditorPanel>
  );
}
