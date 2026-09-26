import type { Layer, LayerMapping } from '@green/api-client';
import { Select, Text } from '@green/ui';
import type { FC } from 'react';
import { selectPlanningBoundary } from '../model/layerKinds';

interface BoundaryCandidatePickerProps {
  layers: Layer[];
  mappings: Record<string, LayerMapping>;
  onChange: (next: Record<string, LayerMapping>) => void;
  readOnly?: boolean;
}

const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });

function shortName(name: string) {
  return name.split(/\||\$\d+\$/).at(-1) || name;
}

export const BoundaryCandidatePicker: FC<BoundaryCandidatePickerProps> = ({
  layers,
  mappings,
  onChange,
  readOnly = false,
}) => {
  const candidates = layers
    .filter((layer) => layer.boundary_candidate?.status === 'usable')
    .sort(
      (left, right) =>
        (right.boundary_candidate?.inset_1_5m_area_m2 ?? 0) -
        (left.boundary_candidate?.inset_1_5m_area_m2 ?? 0),
    );
  if (!candidates.length) return null;

  const selected = layers.find(
    (layer) => mappings[layer.id]?.kind === 'site_border',
  );
  const metrics = selected?.boundary_candidate;

  return (
    <section className="min-w-0">
      <label className="block">
        <Text
          as="span"
          variant="body"
          className="mb-2 block font-semibold text-neutral-950"
        >
          Территория расчёта
        </Text>
        <Select
          aria-label="Контур территории"
          disabled={readOnly}
          value={selected?.id ?? ''}
          onChange={(event) => {
            const selectedId = event.target.value;
            onChange(selectPlanningBoundary(mappings, selectedId));
          }}
        >
          <option value="">Выберите контур</option>
          {candidates.map((layer, index) => (
            <option value={layer.id} key={layer.id}>
              {shortName(layer.source_name)}
              {index === 0 ? ' — крупнейший контур' : ''}
            </option>
          ))}
        </Select>
      </label>
      {selected && metrics?.status === 'usable' && (
        <Text as="p" variant="caption" className="mt-2 text-neutral-600">
          {number.format(metrics.area_m2)} м²
          {', '}
          после внутреннего отступа {number.format(
            metrics.inset_1_5m_area_m2,
          )}{' '}
          м²
        </Text>
      )}
    </section>
  );
};
