import { useMemo, useState } from 'react';
import type { PlanObject, SpeciesShortlistItem } from '@green/api-client';
import { Button, Combobox, FormField, InlineMessage, Select } from '@green/ui';
import { InspectorHeader } from './InspectorHeader';
import { InspectorFooter } from './InspectorLayout';

export function SpeciesAssignmentPanel({ objects, shortlist, loading, previewing, error, onAssign, onCancel }: {
  objects: PlanObject[];
  shortlist?: SpeciesShortlistItem[];
  loading?: boolean;
  previewing?: boolean;
  error?: string;
  onAssign: (revisionId: string, sizeClass: 'sapling' | 'standard' | 'large') => void;
  onCancel: () => void;
}) {
  const [revisionId, setRevisionId] = useState('');
  const [sizeClass, setSizeClass] = useState<'sapling' | 'standard' | 'large'>('standard');
  const selected = shortlist?.find((item) => item.species.id === revisionId);
  const options = useMemo(() => (shortlist ?? []).map((item) => ({
    value: item.species.id,
    label: item.species.common_name,
    description: item.species.scientific_name,
  })), [shortlist]);
  const mixedKinds = new Set(objects.map((object) => object.kind)).size > 1;

  return <div className="project-inspector species-assignment-panel">
    <InspectorHeader title="Назначить породу" meta={objects.length === 1 ? 'Одна посадка' : `${objects.length} посадок`} />
    <div className="species-assignment-panel__content">
      {mixedKinds ? <InlineMessage tone="warning">Выберите только деревья или только кустарники.</InlineMessage> : null}
      {!mixedKinds ? <FormField label="Порода" hint="Поиск по русскому или латинскому названию"><Combobox value={revisionId} options={options} disabled={loading} placeholder={loading ? 'Загружаем каталог' : 'Начните вводить название'} onChange={setRevisionId} /></FormField> : null}
      {!mixedKinds ? <FormField label="Посадочный материал"><Select value={sizeClass} onChange={(event) => setSizeClass(event.target.value as typeof sizeClass)}><option value="sapling">Саженец</option><option value="standard">Стандартный</option><option value="large">Крупномер</option></Select></FormField> : null}
      {selected ? <section className="species-assignment-panel__details">
        <strong>{selected.species.common_name}</strong>
        <span>{selected.species.scientific_name}</span>
        <dl><dt>Высота</dt><dd>{selected.species.mature_height_min_m}–{selected.species.mature_height_max_m} м</dd><dt>Крона</dt><dd>{selected.species.mature_crown_diameter_min_m}–{selected.species.mature_crown_diameter_max_m} м</dd><dt>Корни</dt><dd>{selected.species.root_architecture === 'shallow' ? 'поверхностные' : selected.species.root_architecture === 'deep' ? 'глубокие' : selected.species.root_architecture === 'mixed' ? 'смешанные' : 'не определены'}</dd></dl>
        {(selected.reasons ?? []).map((reason) => <p key={reason}>{reason}</p>)}
        <InlineMessage tone="info">Прогноз кроны и корней — диапазон для проверки, не нормативная зона.</InlineMessage>
      </section> : null}
      {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    </div>
    <div className="inspector-spacer" />
    <InspectorFooter><Button variant="secondary" disabled={previewing} onClick={onCancel}>Отмена</Button><Button variant="primary" loading={previewing} disabled={!revisionId || mixedKinds} onClick={() => onAssign(revisionId, sizeClass)}>Показать</Button></InspectorFooter>
  </div>;
}
