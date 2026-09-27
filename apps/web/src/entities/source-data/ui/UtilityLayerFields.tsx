import type { LayerMapping } from '@green/api-client';
import { Button, Disclosure, TextInput, Select } from '@green/ui';
import { EMPTY_UTILITY_CONTEXT } from '../model/layerKinds';

type Context = NonNullable<LayerMapping['utility_context']>;
interface Props {
  mapping: LayerMapping;
  onChange: (next: LayerMapping) => void;
}
const NETWORK_TYPES = {
  unknown: 'Не выбран',
  water: 'Водопровод',
  sewer: 'Канализация',
  heat: 'Теплосеть',
  gas: 'Газопровод',
  drainage: 'Дренаж',
  power_cable: 'Электрокабель',
  communication_cable: 'Кабель связи',
} as const;
const REFERENCES = {
  unknown: 'Не выбран',
  axis: 'Ось сети',
  outer_surface: 'Наружная поверхность',
  channel_wall: 'Стенка канала',
  protective_casing: 'Защитный футляр',
} as const;
const INSTALLATIONS = {
  unknown: 'Не выбран',
  underground: 'Подземная',
  aboveground: 'Надземная',
} as const;

export function UtilityLayerFields({ mapping, onChange }: Props) {
  const context = mapping.utility_context ?? EMPTY_UTILITY_CONTEXT;
  const update = (patch: Partial<Context>) =>
    onChange({
      ...mapping,
      category:
        patch.network_type && patch.network_type !== context.network_type
          ? null
          : mapping.category,
      utility_context: { ...context, ...patch, review_status: 'unconfirmed' },
    });
  const complete =
    context.network_type &&
    context.network_type !== 'unknown' &&
    context.geometry_reference &&
    context.geometry_reference !== 'unknown' &&
    context.installation &&
    context.installation !== 'unknown' &&
    context.source_reference?.trim() &&
    context.confirmed_by?.trim();
  return (
    <Disclosure
      variant="plain"
      title={
        context.review_status === 'confirmed'
          ? 'Параметры сети подтверждены'
          : 'Дополнительные сведения'
      }
    >
      <div className="space-y-2 text-xs">
        <div className="text-neutral-600">Расчёт доступен без заполнения</div>
        <label className="block">
          Тип сети
          <Select
            aria-label="Тип сети"
            value={context.network_type ?? 'unknown'}
            onChange={(e) =>
              update({
                network_type: e.target.value as Context['network_type'],
              })
            }
          >
            {Object.entries(NETWORK_TYPES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </label>
        <label className="block">
          Прокладка
          <Select
            aria-label="Прокладка"
            value={context.installation ?? 'unknown'}
            onChange={(e) =>
              update({
                installation: e.target.value as Context['installation'],
              })
            }
          >
            {Object.entries(INSTALLATIONS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </label>
        <label className="block">
          Что обозначает линия
          <Select
            aria-label="Что обозначает линия"
            value={context.geometry_reference ?? 'unknown'}
            onChange={(e) =>
              update({
                geometry_reference: e.target
                  .value as Context['geometry_reference'],
              })
            }
          >
            {Object.entries(REFERENCES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </label>
        <label className="block">
          Источник сведений
          <TextInput
            value={context.source_reference ?? ''}
            maxLength={500}
            onChange={(e) => update({ source_reference: e.target.value })}
          />
        </label>
        <label className="block">
          Кто подтвердил
          <TextInput
            value={context.confirmed_by ?? ''}
            maxLength={160}
            onChange={(e) => update({ confirmed_by: e.target.value })}
          />
        </label>
        {context.review_status !== 'confirmed' && (
          <Button
            controlSize="compact"
            disabled={!complete}
            onClick={() =>
              onChange({
                ...mapping,
                utility_context: { ...context, review_status: 'confirmed' },
              })
            }
          >
            Подтвердить параметры
          </Button>
        )}
      </div>
    </Disclosure>
  );
}
