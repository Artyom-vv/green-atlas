import type { LayerMapping } from '@green/api-client';
import { Button, Checkbox, Disclosure, TextInput, Select } from '@green/ui';
import { EMPTY_UTILITY_CONTEXT } from '../model/layerKinds';

type Context = NonNullable<LayerMapping['utility_context']>;
interface Props {
  mapping: LayerMapping;
  onChange: (next: LayerMapping) => void;
  embedded?: boolean;
  readOnly?: boolean;
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

/** Embedded fields share the row's one disclosure; legacy callers keep their own disclosure. */
export function UtilityLayerFields({
  mapping,
  onChange,
  embedded = false,
  readOnly = false,
}: Props) {
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
  const complete = Boolean(
    context.network_type &&
    context.network_type !== 'unknown' &&
    context.geometry_reference &&
    context.geometry_reference !== 'unknown' &&
    context.installation &&
    context.installation !== 'unknown' &&
    context.source_reference?.trim() &&
    context.confirmed_by?.trim(),
  );
  const content = (
    <div className="min-w-0 space-y-4 text-sm">
      {embedded && (
        <h3 className="m-0 text-sm font-medium">Параметры инженерной сети</h3>
      )}
      <p className="m-0 text-xs text-neutral-500">
        Расчёт доступен без заполнения
      </p>
      <div className="grid min-w-0 gap-4 @xl:grid-cols-2">
        <label className="grid min-w-0 gap-2">
          <span className="text-xs text-neutral-600">Тип сети</span>
          <Select
            aria-label="Тип сети"
            controlSize="default"
            disabled={readOnly}
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
        <label className="grid min-w-0 gap-2">
          <span className="text-xs text-neutral-600">Прокладка</span>
          <Select
            aria-label="Прокладка"
            controlSize="default"
            disabled={readOnly}
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
        <label className="grid min-w-0 gap-2">
          <span className="text-xs text-neutral-600">Что обозначает линия</span>
          <Select
            aria-label="Что обозначает линия"
            controlSize="default"
            disabled={readOnly}
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
        <label className="grid min-w-0 gap-2">
          <span className="text-xs text-neutral-600">Источник сведений</span>
          <TextInput
            controlSize="default"
            readOnly={readOnly}
            value={context.source_reference ?? ''}
            maxLength={500}
            onChange={(e) => update({ source_reference: e.target.value })}
          />
        </label>
        <label className="grid min-w-0 gap-2">
          <span className="text-xs text-neutral-600">Кто подтвердил</span>
          <TextInput
            controlSize="default"
            readOnly={readOnly}
            value={context.confirmed_by ?? ''}
            maxLength={160}
            onChange={(e) => update({ confirmed_by: e.target.value })}
          />
        </label>
      </div>
      {embedded
        ? complete && (
            <Checkbox
              label="Подтверждаю параметры сети по указанному источнику"
              controlSize="default"
              disabled={readOnly}
              checked={context.review_status === 'confirmed'}
              onChange={(event) =>
                onChange({
                  ...mapping,
                  utility_context: {
                    ...context,
                    review_status: event.target.checked
                      ? 'confirmed'
                      : 'unconfirmed',
                  },
                })
              }
            />
          )
        : !readOnly &&
          context.review_status !== 'confirmed' && (
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
  );
  if (embedded) return content;
  return (
    <Disclosure
      variant="plain"
      title={
        context.review_status === 'confirmed'
          ? 'Параметры сети подтверждены'
          : 'Дополнительные сведения'
      }
    >
      {content}
    </Disclosure>
  );
}
