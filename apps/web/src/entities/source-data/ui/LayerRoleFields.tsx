import type { Layer, LayerKind, LayerMapping, LayerRecognition } from '@green/api-client';
import { Button, Combobox, Disclosure, Select } from '@green/ui';
import {
  changeLayerRole,
  LAYER_KIND_LABELS,
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

export function LayerRoleFields({ layer, mapping, recognition, onChange }: Props) {
  const categories = recognition?.categories ?? [];
  const proposal = recognition?.proposals.find((item) => item.layer_id === layer.id);
  const suggested = categories.find((item) => item.category === proposal?.category);
  const selected = categories.find((item) => item.category === mapping.category);
  const descriptive = selected?.kind === null
    ? selected
    : suggested?.kind === null
      ? suggested
      : undefined;
  const pending = mapping.confirmed === false;
  const legacyBoundaryChoice =
    layer.suggested_kind === 'site_border' && !layer.boundary_candidate;
  const suggestedRole =
    suggested?.kind && suggested.kind !== 'site_border' && proposal?.confidence !== 'low'
      ? suggested.kind
      : proposal?.calculation_role &&
          proposal.calculation_role !== 'ignore' &&
          proposal?.calculation_role !== 'site_border' &&
          proposal?.confidence !== 'low'
        ? proposal?.calculation_role
        : !suggested && mapping.kind !== 'ignore' && mapping.kind !== 'site_border'
          ? mapping.kind
          : null;
  const roleChoices = Array.from(new Set([
    ...(suggestedRole ? [suggestedRole] : []),
    ...(proposal?.review_roles ?? []),
  ])).filter((kind): kind is LayerKind =>
    kind !== 'site_border' && kind !== 'ignore',
  ).slice(0, 3);
  const chooseRole = (kind: LayerKind, category?: typeof mapping.category) => {
    if (kind === 'site_border' && !legacyBoundaryChoice) return;
    const candidate = category === undefined
      ? (descriptive ?? selected ?? suggested)
      : categories.find((item) => item.category === category);
    const matchingCategory = candidate?.kind === null || candidate?.kind === kind
      ? candidate.category
      : null;
    onChange(changeLayerRole(mapping, kind, matchingCategory));
  };
  const currentLabel = LAYER_KIND_LABELS[mapping.kind];

  return (
    <div className="grid min-w-0 gap-2 text-sm">
      {pending ? (
        <>
          <div className="font-medium text-neutral-800">
            {descriptive
              ? 'Как учитывать весь слой при размещении посадок?'
              : proposal?.review_question ??
              (suggested?.kind === 'site_border'
                ? 'Какой контур задаёт территорию расчёта?'
                : suggested
                  ? `Как учитывать слой «${suggested.label}»?`
                  : 'Что находится на этом слое?')}
          </div>
          {suggested && (
            <div className="text-neutral-600">На чертеже: {selected?.label ?? suggested.label}</div>
          )}
          {descriptive && (
            <p role="status" className="m-0 text-xs leading-5 text-amber-800">
              {descriptive.category === 'mixed_source'
                ? `Тип уже распознан, но в слое ${layer.object_count.toLocaleString('ru-RU')} объектов разных типов. Описание «${descriptive.label}» не задаёт им единую роль в расчёте. Поэтому слой остаётся на проверке.`
                : `Тип «${descriptive.label}» описывает содержимое, но не определяет, как оно влияет на посадки. Поэтому слой остаётся на проверке.`}
            </p>
          )}
          {suggested?.kind === 'site_border' ? (
            <p className="m-0 text-neutral-600">
              Выберите контур в разделе «Территория»
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {roleChoices.map((kind) => (
                <Button
                  key={kind}
                  controlSize="compact"
                  variant={kind === suggestedRole ? 'secondary' : 'ghost'}
                  onClick={() => chooseRole(kind)}
                >
                  {kind === suggestedRole && descriptive
                    ? `Принять роль: ${LAYER_KIND_LABELS[kind]}`
                    : kind === suggestedRole
                      ? `Подтвердить: ${LAYER_KIND_LABELS[kind]}`
                      : LAYER_KIND_LABELS[kind]}
                </Button>
              ))}
            </div>
          )}
        </>
      ) : (
        <div className="grid gap-1">
          <span className="font-medium text-neutral-800">{currentLabel}</span>
          <span className="text-xs text-neutral-600">
            {mapping.kind === 'ignore'
              ? 'Не влияет на проверку посадок'
              : mapping.kind === 'site_border'
                ? 'Задаёт предел территории расчёта'
                : 'Геометрия участвует в проверке мест посадки'}
          </span>
          {selected && (
            <span className="text-xs text-neutral-600">
              На чертеже: {selected.label}
            </span>
          )}
        </div>
      )}
      <Disclosure
        variant="plain"
        title={pending ? 'Указать влияние на посадки' : 'Изменить назначение'}
        contentClassName="max-w-xl"
      >
        <div className="grid gap-3 border-0 border-t border-solid border-neutral-200 pt-3">
          <label className="grid gap-1">
            <span className="text-xs font-medium text-neutral-700">Как учитывать все объекты слоя</span>
            <Select
              aria-label={`Участие в расчёте ${layer.source_name}`}
              value={pending ? 'unassigned' : mapping.kind}
              onChange={(event) => {
                const kind = layerKindFromValue(event.target.value);
                if (kind) chooseRole(kind);
              }}
            >
              <option value="unassigned" disabled>Назначение не определено</option>
              {(mapping.kind === 'site_border' || legacyBoundaryChoice) && (
                <option value="site_border" disabled={!legacyBoundaryChoice}>Граница участка</option>
              )}
              {LAYER_KIND_OPTIONS.filter((item) => item.value !== 'site_border').map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </Select>
            <span className="text-xs leading-5 text-neutral-600">
              Это решение меняет проверку мест посадки и применяется ко всем{' '}
              {layer.object_count.toLocaleString('ru-RU')} объектам слоя
            </span>
          </label>
          {categories.length > 0 ? (
            <Disclosure variant="plain" title="Исправить распознанный тип">
              <div className="grid gap-1">
                <span className="text-xs font-medium text-neutral-700">Что показано на чертеже</span>
                <Combobox
                  aria-label={`Найти тип объектов ${layer.source_name}`}
                  placeholder="Найти тип в классификаторе"
                  value={mapping.category ?? undefined}
                  options={categories
                    .filter((item) => item.kind !== 'site_border')
                    .map((item) => ({
                      value: item.category,
                      label: item.label,
                      description: item.kind
                        ? `Для расчёта: ${LAYER_KIND_LABELS[item.kind]}`
                        : 'Только описание, не завершает проверку',
                    }))}
                  onChange={(value) => {
                    const item = categories.find((candidate) => candidate.category === value);
                    if (!item) return;
                    if (item.kind === null)
                      onChange({ ...mapping, category: item.category, confirmed: false });
                    else chooseRole(item.kind, item.category);
                  }}
                />
                <span className="text-xs leading-5 text-neutral-600">
                  Описание помогает понять слой, но без расчётной роли не снимает проверку
                </span>
              </div>
            </Disclosure>
          ) : (
            <p className="m-0 text-xs text-neutral-600">
              Точный тип станет доступен после распознавания слоёв
            </p>
          )}
        </div>
      </Disclosure>
      {proposal && (
        <Disclosure variant="plain" title="Почему предложено">
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
