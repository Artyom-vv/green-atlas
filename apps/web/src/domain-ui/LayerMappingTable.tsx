import type { CSSProperties } from 'react';
import type { Layer, LayerKind, LayerMapping } from '@green/api-client';
import { DataTable, Select } from '@green/ui';

const options: Array<{ value: LayerKind; label: string }> = [
  { value: 'site_border', label: 'Граница участка' },
  { value: 'building', label: 'Здание' },
  { value: 'road', label: 'Дорога / проезд' },
  { value: 'utility', label: 'Инженерная сеть' },
  { value: 'existing_green', label: 'Существующее озеленение' },
  { value: 'water', label: 'Водный объект' },
  { value: 'restricted', label: 'Техническая / непригодная зона' },
  { value: 'ignore', label: 'Не использовать' },
];

export function LayerMappingTable({ layers, mappings, onChange, readOnly = false }: { layers: Layer[]; mappings: Record<string, LayerMapping>; onChange: (next: Record<string, LayerMapping>) => void; readOnly?: boolean }) {
  const update = (layerId: string, patch: Partial<LayerMapping>) => onChange({ ...mappings, [layerId]: { ...mappings[layerId], layer_id: layerId, ...patch } as LayerMapping });

  return (
    <DataTable className="mapping-table">
      <thead><tr><th>Слой DXF</th><th>Использовать как</th><th>Объектов</th></tr></thead>
      <tbody>
        {layers.map((layer) => {
          const mapping = mappings[layer.id];
          return (
            <tr key={layer.id} className={((!mapping?.kind || mapping.kind === 'ignore') && layer.required) || !layer.geometry_complete ? 'row-warning' : undefined}>
              <td><span className="layer-name"><span className="layer-swatch" style={{ '--layer-color': layer.color } as CSSProperties} /><code>{layer.source_name}</code>{layer.required ? <small>нужен для границы</small> : null}{!layer.geometry_complete ? <small>часть объектов не показана</small> : null}</span></td>
              <td>{readOnly ? options.find(option => option.value === (mapping?.kind ?? layer.mapped_kind ?? 'ignore'))?.label : <Select aria-label={`Тип слоя ${layer.source_name}`} value={mapping?.kind ?? 'ignore'} onChange={(event) => update(layer.id, { kind: event.target.value as LayerKind })}>{options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</Select>}</td>
              <td className="mono-cell">{layer.object_count}</td>
            </tr>
          );
        })}
      </tbody>
    </DataTable>
  );
}
