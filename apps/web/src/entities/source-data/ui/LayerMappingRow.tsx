/** Исходные данные: тип слоя, физическая роль и участие границы в подготовке */
import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
import { Button, cx, IconButton, Select } from '@green/ui';
import { ChevronDown, ChevronRight, CircleCheck } from 'lucide-react';
import { useId, useState } from 'react';
import {
  LAYER_KIND_LABELS,
  LAYER_KIND_OPTIONS,
  layerKindFromValue,
} from '../model/layerKinds';
import { LayerMappingDetails } from './LayerMappingDetails';
import { LAYER_MAPPING_COLUMNS } from './layerMappingLayout';
import {
  layerDisplayName,
  layerMappingPresentation,
  layerObjectCount,
} from './layerMappingPresentation';

interface Props {
  layer: Layer;
  mapping: LayerMapping;
  recognition?: LayerRecognition;
  readOnly?: boolean;
  disabled?: boolean;
  onChange: (next: LayerMapping) => void;
  onBoundaryReview?: () => void;
}

export function LayerMappingRow({
  layer,
  mapping,
  recognition,
  readOnly,
  disabled,
  onChange,
  onBoundaryReview,
}: Props) {
  const id = useId();
  const detailsId = `${id}-details`;
  const [expanded, setExpanded] = useState(false);
  const display = layerDisplayName(layer.source_name);
  const presentation = layerMappingPresentation(layer, mapping, recognition);
  const {
    categories,
    category,
    role,
    pending,
    boundary,
    legacyBoundaryChoice,
    suggestedRole,
    topologyIntent,
    objectRoles,
  } = presentation;
  const editableCategories = categories.filter(
    (item) => item.kind !== 'site_border',
  );
  const change = (next: LayerMapping) => {
    if (readOnly || disabled) return;
    if (mapping.kind !== 'utility' && next.kind === 'utility')
      setExpanded(true);
    onChange(next);
  };
  return (
    <li className="min-w-0 border-b border-neutral-200 bg-white px-4 py-3 last:border-b-0 min-[720px]:px-6">
      <div
        className={cx(
          'grid min-w-0 grid-cols-[minmax(0,1fr)_minmax(0,1fr)_36px] items-center gap-x-4 gap-y-3',
          LAYER_MAPPING_COLUMNS,
        )}
      >
        <div className="col-span-2 col-start-1 row-start-1 grid min-h-9 min-w-0 content-center @3xl:col-span-1">
          <div
            className="text-sm leading-5 font-medium wrap-anywhere"
            title={layer.source_name}
          >
            {display.name}
          </div>
          <span className="text-xs leading-4 text-neutral-500 tabular-nums">
            {layerObjectCount(layer.object_count)}
          </span>
        </div>
        <div className="col-start-1 min-w-0 @3xl:col-start-2 @3xl:row-start-1">
          <span className="mb-2 block text-xs text-neutral-500 @3xl:hidden">
            Тип на чертеже
          </span>
          {!readOnly && !boundary && editableCategories.length ? (
            <Select
              aria-label={`Тип на чертеже ${layer.source_name}`}
              disabled={disabled}
              controlSize="default"
              value={category?.category ?? ''}
              onChange={(event) => {
                const selected = editableCategories.find(
                  (item) => item.category === event.target.value,
                );
                if (!selected) return;
                change(
                  selected.kind === null
                    ? {
                        ...mapping,
                        category: selected.category,
                        confirmed: false,
                      }
                    : presentation.chooseRole(selected.kind, selected.category),
                );
              }}
            >
              <option value="" disabled>
                Не уточнён
              </option>
              {editableCategories.map((item) => (
                <option key={item.category} value={item.category}>
                  {item.label}
                </option>
              ))}
            </Select>
          ) : (
            <div className="flex min-h-9 items-center text-sm text-neutral-700">
              {category?.label ?? 'Не уточнён'}
            </div>
          )}
        </div>
        <div className="col-start-2 min-w-0 @3xl:col-start-3 @3xl:row-start-1">
          <span className="mb-2 block text-xs text-neutral-500 @3xl:hidden">
            Роль в расчёте
          </span>
          {topologyIntent ? (
            <div className="flex min-h-9 items-center text-sm text-neutral-700">
              Граница для замыкания
            </div>
          ) : objectRoles ? (
            <div className="flex min-h-9 items-center text-sm text-neutral-700">
              По объектам
            </div>
          ) : !readOnly && (!boundary || legacyBoundaryChoice) ? (
            <Select
              aria-label={`Роль в расчёте ${layer.source_name}`}
              disabled={disabled}
              controlSize="default"
              value={role ?? ''}
              onChange={(event) => {
                const nextRole = layerKindFromValue(event.target.value);
                if (
                  nextRole &&
                  (nextRole !== 'site_border' || legacyBoundaryChoice)
                )
                  change(presentation.chooseRole(nextRole));
              }}
            >
              <option value="" disabled>
                Не определена
              </option>
              {legacyBoundaryChoice && (
                <option value="site_border">Граница участка</option>
              )}
              {LAYER_KIND_OPTIONS.filter(
                (item) => item.value !== 'site_border',
              ).map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </Select>
          ) : (
            <div className="flex min-h-9 items-center text-sm text-neutral-700">
              {boundary
                ? mapping.kind === 'site_border' && mapping.confirmed === true
                  ? 'Граница участка'
                  : 'Не выбрана'
                : role
                  ? LAYER_KIND_LABELS[role]
                  : 'Не определена'}
            </div>
          )}
        </div>
        <div className="col-span-2 col-start-1 min-w-0 @3xl:col-span-1 @3xl:col-start-4 @3xl:row-start-1">
          {mapping.confirmed === true ? (
            <span className="flex min-h-9 items-center gap-2 text-sm text-green-700">
              <CircleCheck className="size-4 shrink-0" aria-hidden="true" />
              Принято
            </span>
          ) : topologyIntent ? (
            <span className="flex min-h-9 items-center text-sm text-neutral-500">
              Для подготовки
            </span>
          ) : boundary && onBoundaryReview ? (
            <Button
              variant="secondary"
              controlSize="default"
              className="w-full"
              aria-label={`Выбрать границу в разделе Геометрия ${layer.source_name}`}
              onClick={onBoundaryReview}
            >
              Геометрия
            </Button>
          ) : !readOnly && pending && suggestedRole ? (
            <Button
              variant="primary"
              controlSize="default"
              className="w-full"
              aria-label={`Принять назначение ${display.name}`}
              disabled={disabled}
              onClick={() => change(presentation.chooseRole(suggestedRole))}
            >
              Принять
            </Button>
          ) : !pending ? (
            <span className="flex min-h-9 items-center text-sm text-neutral-500">
              Назначение задано
            </span>
          ) : (
            <span className="text-warning-strong flex min-h-9 items-center text-sm">
              {boundary
                ? 'Выберите границу'
                : objectRoles
                  ? 'Проверить объекты'
                  : 'Требует решения'}
            </span>
          )}
        </div>
        <IconButton
          className="col-start-3 row-start-1 @3xl:col-start-5"
          icon={expanded ? ChevronDown : ChevronRight}
          variant="ghost"
          controlSize="default"
          label={`${mapping.kind === 'utility' ? 'Сведения и параметры слоя' : 'Сведения о слое'} ${display.name}`}
          aria-expanded={expanded}
          aria-controls={detailsId}
          onClick={() => setExpanded((open) => !open)}
        />
      </div>
      {expanded ? (
        <LayerMappingDetails
          id={detailsId}
          layer={layer}
          mapping={mapping}
          recognition={recognition}
          readOnly={readOnly || disabled}
          onChange={onChange}
          onBoundaryReview={onBoundaryReview}
        />
      ) : (
        <div id={detailsId} hidden />
      )}
    </li>
  );
}
