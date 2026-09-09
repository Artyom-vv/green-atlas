import { useEffect, useState } from 'react';
import type { PlanObject, SpeciesShortlistItem } from '@green/api-client';
import { Button, InlineMessage, Select } from '@green/ui';
import { SpeciesCatalog, SpeciesPhoto } from './SpeciesPicker';
import { EditorActions, EditorDisclosure, EditorField, EditorPanel } from './EditorPanel';

export function SpeciesAssignmentPanel({ objects, shortlist, loading, previewing, error, onAssign, onSelectKind, onCancel, onCatalogModeChange }: {
  objects: PlanObject[];
  shortlist?: SpeciesShortlistItem[];
  loading?: boolean;
  previewing?: boolean;
  error?: string;
  onAssign: (revisionId: string, sizeClass: 'sapling' | 'standard' | 'large') => void;
  onCancel: () => void;
  onSelectKind?: (kind: PlanObject['kind']) => void;
  onCatalogModeChange?: (browsing: boolean) => void;
}) {
  const [revisionId, setRevisionId] = useState('');
  const [sizeClass, setSizeClass] = useState<'sapling' | 'standard' | 'large'>('standard');
  const selected = shortlist?.find((item) => item.species.id === revisionId);
  const mixedKinds = new Set(objects.map((object) => object.kind)).size > 1;
  const browsing = !selected && !mixedKinds;
  useEffect(() => onCatalogModeChange?.(browsing), [browsing, onCatalogModeChange]);

  return <EditorPanel title="Назначить породу">
    <p className="editor-panel__summary">Выбрано {objects.length}</p>
      {mixedKinds ? <section className="editor-panel__section"><p>Кому назначить вид?</p><EditorActions grid>{(['tree', 'shrub'] as const).map(kind => <Button variant="secondary" key={kind} disabled={previewing || !onSelectKind} onClick={() => { setRevisionId(''); onSelectKind?.(kind); }}>{kind === 'tree' ? 'Деревьям' : 'Кустарникам'} ({objects.filter(object => object.kind === kind).length})</Button>)}</EditorActions></section> : null}
      {browsing ? <SpeciesCatalog loading={loading} species={(shortlist ?? []).map(item => item.species)} value={revisionId} disabled={loading || previewing} onChange={setRevisionId} /> : null}
      {selected && !mixedKinds ? <section className="species-assignment-selection">
        <div className="species-assignment-identity"><SpeciesPhoto key={selected.species.id} species={selected.species} credits /><div><h3>{selected.species.common_name}</h3><p>{selected.species.scientific_name}</p><Button variant="secondary" disabled={previewing} onClick={() => setRevisionId('')}>Выбрать другую породу</Button></div></div>
        <EditorField label="Материал"><Select aria-label="Посадочный материал" value={sizeClass} onChange={(event) => setSizeClass(event.target.value as typeof sizeClass)}><option value="sapling">Саженец</option><option value="standard">Стандартный</option><option value="large">Крупномер</option></Select></EditorField>
        <EditorDisclosure title="Размеры взрослого растения">
        <dl className="editor-panel__metrics"><dt>Высота</dt><dd>{selected.species.mature_height_min_m}–{selected.species.mature_height_max_m} м</dd><dt>Крона</dt><dd>{selected.species.mature_crown_diameter_min_m}–{selected.species.mature_crown_diameter_max_m} м</dd><dt>Корни</dt><dd>{selected.species.root_architecture === 'shallow' ? 'поверхностные' : selected.species.root_architecture === 'deep' ? 'глубокие' : selected.species.root_architecture === 'mixed' ? 'смешанные' : 'не определены'}</dd></dl>
        {selected.reasons?.length ? <EditorDisclosure title="Почему подходит">{selected.reasons.map(reason => <p key={reason}>{reason}</p>)}</EditorDisclosure> : null}
        <p className="editor-panel__hint">Прогнозные размеры, не нормативные отступы.</p>
        </EditorDisclosure>
      </section> : null}
      {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    <EditorActions><Button variant="secondary" disabled={previewing} onClick={onCancel}>Отмена</Button>{selected && !mixedKinds ? <Button variant="primary" loading={previewing} disabled={loading || objects.some(object => object.locked)} onClick={() => onAssign(revisionId, sizeClass)}>Проверить замену</Button> : null}</EditorActions>
  </EditorPanel>;
}
