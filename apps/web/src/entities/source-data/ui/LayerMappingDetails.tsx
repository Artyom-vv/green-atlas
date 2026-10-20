/** Исходные данные: сведения о назначении слоя без подмены геометрической проверки */
import type { Layer, LayerMapping, LayerRecognition } from '@green/api-client';
import { Button, cx } from '@green/ui';
import type { CSSProperties } from 'react';
import {
  layerDisplayName,
  layerMappingPresentation,
} from './layerMappingPresentation';
import { UtilityLayerFields } from './UtilityLayerFields';
import { LAYER_MAPPING_COLUMNS } from './layerMappingLayout';

interface Props {
  id: string;
  layer: Layer;
  mapping: LayerMapping;
  recognition?: LayerRecognition;
  readOnly?: boolean;
  onChange: (next: LayerMapping) => void;
  onBoundaryReview?: () => void;
}

export function LayerMappingDetails({
  id,
  layer,
  mapping,
  recognition,
  readOnly,
  onChange,
  onBoundaryReview,
}: Props) {
  const display = layerDisplayName(layer.source_name);
  const { proposal, suggested, boundary, topologyIntent, objectRoles } =
    layerMappingPresentation(layer, mapping, recognition);
  const evidence = proposal?.evidence ?? layer.suggestion_reasons ?? [];
  const unresolved = proposal?.unresolved ?? [];
  return (
    <div
      id={id}
      role="region"
      aria-label={`Сведения о слое ${display.name}`}
      className={cx(
        'grid grid-cols-1 gap-x-4 gap-y-6 pt-4',
        LAYER_MAPPING_COLUMNS,
      )}
    >
      <div className="col-start-1 grid min-w-0 grid-cols-1 gap-y-4 @3xl:col-span-4 @3xl:grid-cols-subgrid">
        {mapping.kind === 'utility' && (
          <div className="col-span-full min-w-0">
            <UtilityLayerFields
              embedded
              readOnly={readOnly}
              mapping={mapping}
              onChange={onChange}
            />
          </div>
        )}
        <dl className="col-span-full m-0 grid min-w-0 grid-cols-1 gap-x-4 gap-y-2 text-sm leading-5 @xl:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] @3xl:grid-cols-subgrid [&>dd]:m-0 [&>dd]:min-w-0 [&>dd]:wrap-anywhere @3xl:[&>dd]:col-span-3 [&>dt]:text-neutral-500">
          {boundary && (
            <>
              <dt>Граница расчёта</dt>
              <dd>
                {onBoundaryReview && (
                  <Button
                    variant="ghost"
                    controlSize="compact"
                    className="-my-1 -ml-3 text-blue-600"
                    onClick={onBoundaryReview}
                  >
                    К геометрии
                  </Button>
                )}
              </dd>
            </>
          )}
          {topologyIntent && (
            <>
              <dt>Подготовка</dt>
              <dd>Граница для замыкания</dd>
            </>
          )}
          {objectRoles && (
            <>
              <dt>Назначение</dt>
              <dd>Назначения задаются отдельным объектам</dd>
            </>
          )}
          {mapping.kind === 'ignore' && !topologyIntent && !objectRoles && (
            <>
              <dt>Участие в расчёте</dt>
              <dd>
                {mapping.confirmed === true
                  ? 'Исключён из проверки посадок'
                  : 'Роль не выбрана'}
              </dd>
            </>
          )}
          {layer.required && (
            <>
              <dt>Обязательный слой</dt>
              <dd>Нужен для границы расчёта</dd>
            </>
          )}
          {!layer.geometry_complete && (
            <>
              <dt>Геометрия</dt>
              <dd>Есть пропуски, сохранены в отчёте исходных данных</dd>
            </>
          )}
          <dt className="text-neutral-500">Источник</dt>
          <dd className="m-0 wrap-anywhere">{display.origin}</dd>
          {display.name !== layer.source_name && (
            <>
              <dt className="text-neutral-500">Полное имя</dt>
              <dd className="m-0 wrap-anywhere">{layer.source_name}</dd>
            </>
          )}
          <dt className="text-neutral-500">Цвет CAD</dt>
          <dd className="m-0 flex items-center gap-2">
            <span
              aria-hidden="true"
              className="size-3 shrink-0 border-2 border-(--layer-color)"
              style={{ '--layer-color': layer.color } as CSSProperties}
            />
            {layer.color}
          </dd>
          {suggested && (
            <>
              <dt className="text-neutral-500">Предложение</dt>
              <dd className="m-0 wrap-anywhere">{suggested.label}</dd>
            </>
          )}
          {!!evidence.length && (
            <>
              <dt className="text-neutral-500">Основание</dt>
              <dd className="m-0">
                <ul className="m-0 list-none space-y-1 p-0">
                  {evidence.map((reason) => (
                    <li key={reason} className="wrap-anywhere">
                      {reason}
                    </li>
                  ))}
                </ul>
              </dd>
            </>
          )}
          {!!unresolved.length && (
            <>
              <dt className="text-neutral-500">Требует проверки</dt>
              <dd className="m-0">
                <ul className="m-0 list-none space-y-1 p-0">
                  {unresolved.map((reason) => (
                    <li key={reason} className="wrap-anywhere">
                      {reason}
                    </li>
                  ))}
                </ul>
              </dd>
            </>
          )}
        </dl>
      </div>
    </div>
  );
}
