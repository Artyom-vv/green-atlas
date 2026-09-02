import type { PlanObject } from '@green/api-client';
import { Leaf } from 'lucide-react';
import { Button, EmptyState } from '@green/ui';
import { GrowthHorizonControl, type GrowthHorizon } from './GrowthHorizonControl';
import { InspectorHeader } from './InspectorHeader';

export function ObjectInspector({ object, speciesName, growthHorizon, onGrowthHorizon, onSpecies, onDelete, editable = true }: { object?: PlanObject; speciesName?: string; growthHorizon?: GrowthHorizon; onGrowthHorizon: (value: GrowthHorizon) => void; onSpecies: () => void; onDelete: () => void; editable?: boolean }) {
  if (!object) return <div className="inspector-empty"><EmptyState title="Ничего не выбрано" description="Выберите объект на карте, чтобы увидеть параметры" /></div>;
  const status = object.status === 'error'
    ? { className: 'is-error', label: 'Есть нарушение', description: 'Позиция не проходит обязательную геометрическую проверку' }
    : object.status === 'warning'
      ? object.species_revision_id
        ? { className: 'is-warning', label: 'Нужно уточнение', description: 'Проверьте замечания по исходным данным, кроне или корневой зоне' }
        : { className: 'is-warning', label: 'Порода не назначена', description: 'Позиция проверена, но прогноз роста пока недоступен' }
      : { className: 'is-valid', label: 'Размещение допустимо', description: 'Нарушений обязательных расстояний не обнаружено' };
  return (
    <div className="object-inspector">
      <InspectorHeader title={object.kind === 'tree' ? 'Дерево' : 'Кустарник'} meta="Выбранная посадка" />
      <section className="inspector-status"><span>Проверка</span><strong className={status.className}><i />{status.label}</strong><p>{status.description}</p></section>
      <section className="object-species"><h3>Порода</h3><strong>{speciesName ?? 'Не назначена'}</strong><span>{speciesName ? 'Прогноз роста доступен' : 'Назначьте для расчёта кроны и корней'}</span>{editable ? <Button variant="secondary" icon={Leaf} onClick={onSpecies}>{speciesName ? 'Изменить породу' : 'Назначить породу'}</Button> : null}</section>
      {object.canopy_forecast?.length ? <GrowthHorizonControl value={growthHorizon} forecasts={[object]} onChange={onGrowthHorizon} /> : null}
      <div className="inspector-spacer" />
      <footer>{editable ? <button type="button" onClick={onDelete}>Удалить</button> : <Button variant="secondary" disabled>Зафиксировано в реализации</Button>}</footer>
    </div>
  );
}
