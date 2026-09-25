import type { PlacementCheck } from '@green/api-client';
import { Disclosure } from '@green/ui';
import { evidenceMeters } from '../model/evidencePresentation';

type Cause = NonNullable<NonNullable<PlacementCheck['geometry_evidence']>['causes']>[number];
const stages = {
  mapping: 'Назначение слоя', inventory: 'Подготовка объекта', query: 'Запрос к AutoCAD',
  area: 'Внутренняя область', rule: 'Правило посадки', clearance: 'Геометрическое ограничение', site: 'Граница территории',
};

export function GeometryEvidenceList({ causes }: { causes: Cause[] }) {
  const groups = new Map<string, Cause[]>();
  for (const cause of causes) {
    const key = JSON.stringify([cause.source_layer, cause.code, cause.stage, cause.requirement_basis]);
    groups.set(key, [...(groups.get(key) ?? []), cause]);
  }
  return <>{[...groups.entries()].map(([key, items]) => {
    const first = items[0];
    return <Disclosure key={key} title={<span className="grid gap-1 text-left">
      <span>{first.message}</span>
      <span className="text-xs font-normal text-neutral-600">{first.source_layer?.split('|').at(-1) ?? 'Территория'} ({items.length})</span>
    </span>}>
      <div className="grid gap-2 break-words">
        <p className="m-0 text-neutral-600">{stages[first.stage]}</p>
        {first.source_layer && <p className="m-0">{first.source_layer}</p>}
        {first.requirement_basis && <p className="m-0">Основание: {first.requirement_basis === 'roots' ? 'прогноз корней' : first.requirement_basis === 'canopy' ? 'прогноз кроны' : 'правило отступа'}</p>}
        <p className="m-0">{first.action}</p>
        {items.map((cause, index) => <div key={index} className="grid gap-2 border-t border-neutral-200 pt-2">
          {items.length > 1 && <strong>Объект {index + 1}</strong>}
          {cause.measured_distance_m != null && <p className="m-0">Измерено {evidenceMeters(cause.measured_distance_m)}</p>}
          {cause.required_distance_m != null && <p className="m-0">Требуется {evidenceMeters(cause.required_distance_m)}</p>}
          <Disclosure title="Технические сведения">
            <dl className="m-0 grid gap-1">
              <dt>Код причины</dt><dd className="m-0">{cause.code}</dd>
              <dt>Запрос объекта</dt><dd className="m-0">{cause.query_sent ? 'Передан в AutoCAD' : 'Не передан в AutoCAD'}</dd>
              <dt>Идентификаторы</dt><dd className="m-0 break-all">{cause.source_feature_ids?.join(', ') || 'Нет'}</dd>
              {cause.native_error && <><dt>Ответ обработчика</dt><dd className="m-0">{cause.native_error}</dd></>}
            </dl>
          </Disclosure>
        </div>)}
      </div>
    </Disclosure>;
  })}</>;
}
