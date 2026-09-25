import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
import { Button, Disclosure, Select } from '@green/ui';
import {
  changeLayerRole,
  isUnclassifiedMapping,
  LAYER_KIND_OPTIONS,
  layerKindFromValue,
} from '../model/layerKinds';
import { UtilityLayerFields } from './UtilityLayerFields';

interface Props {
  layer: Layer;
  mapping: LayerMapping;
  recognition?: LayerRecognition;
  onChange: (next: LayerMapping) => void;
}

export function LayerRoleFields({
  layer,
  mapping,
  recognition,
  onChange,
}: Props) {
  const categories = recognition?.categories ?? [];
  const proposal = recognition?.proposals.find(
    (item) => item.layer_id === layer.id,
  );
  const suggested = categories.find(
    (item) => item.category === proposal?.category,
  );
  const unclassified = isUnclassifiedMapping(mapping);
  const selected = categories.find((item) => item.category === mapping.category);
  const selectRole = (kind: typeof mapping.kind | null, category: typeof mapping.category) =>
    changeLayerRole(mapping, kind ?? 'restricted', category ?? null);
  return (
    <div className="space-y-2">
      <Select
        aria-label={`Тип слоя ${layer.source_name}`}
        value={
          categories.some((item) => item.category === mapping.category)
            ? `category:${mapping.category}`
            : unclassified ? 'unassigned' : mapping.kind
        }
        onChange={(event) => {
          const category = categories.find(
            (item) => `category:${item.category}` === event.target.value,
          );
          const kind = category ? category.kind : layerKindFromValue(event.target.value);
          if (kind !== undefined)
            onChange(
              selectRole(kind, category?.category),
            );
        }}
      >
        <option value="unassigned" disabled>Назначение не определено</option>
        <optgroup label="Категории">
          {categories.map((item) => (
            <option key={item.category} value={`category:${item.category}`}>
              {item.label}
            </option>
          ))}
        </optgroup>
        <optgroup label="Прочее">
          {LAYER_KIND_OPTIONS.filter((item) => item.value === 'ignore' ||
            (!selected && item.value === mapping.kind) ||
            !categories.some((category) => category.kind === item.value)).map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </optgroup>
      </Select>
      {mapping.confirmed === false && (!unclassified || selected) && (
        <Button
          controlSize="compact"
          variant="ghost"
          onClick={() => onChange(selected
            ? selectRole(selected.kind, selected.category)
            : { ...mapping, confirmed: true })}
        >
          Подтвердить роль
        </Button>
      )}
      {unclassified && !selected && (
        <>
          <div className="text-xs text-amber-700">Нужно определить участие в расчёте</div>
          <Button controlSize="compact" variant="ghost"
            onClick={() => onChange(changeLayerRole(mapping, 'ignore', null))}>
            Исключить из расчёта
          </Button>
        </>
      )}
      {suggested && suggested.category !== mapping.category && (
        <Button
          variant="secondary"
          controlSize="compact"
          onClick={() =>
            onChange(
              selectRole(suggested.kind, suggested.category),
            )
          }
        >
          {`Принять тип: ${suggested.label}`}
        </Button>
      )}
      {proposal && (
        <Disclosure variant="plain" title="Основания предложения">
          <ul className="m-0 space-y-1 pl-4 text-xs">
            {[...proposal.evidence, ...proposal.unresolved].map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </Disclosure>
      )}
      {mapping.kind === 'utility' && (
        <UtilityLayerFields mapping={mapping} onChange={onChange} />
      )}
    </div>
  );
}
