import { useEffect, useRef, useState } from 'react';
import type { BrushPreview, BrushPreviewRequest, BrushStroke, PlantingZoneAssignment, SpeciesRevision } from '@green/api-client';
import { Button, Select } from '@green/ui';
import { SpeciesPicker } from './SpeciesPicker';
import { PlantingZonePicker } from './PlantingZonePicker';
import { EditorActions, EditorDisclosure, EditorField, EditorNumber, EditorPanel } from './EditorPanel';
import type { LiveBrushSettings } from './liveBrushGeometry';

type BrushDraft = Omit<BrushPreviewRequest, 'base_plan_version'>;

export function BrushToolPanel({ species, strokes, zones, zoneIds, width, operation, preview, loading, applying, error, onZoneIdsChange, onWidth, onOperation, onPreview, onApply, onClear, onCancel, settings, onSettingsChange }: {
  species?: SpeciesRevision[];
  strokes: BrushStroke[]; zones: PlantingZoneAssignment[]; zoneIds: string[];
  width: number; operation: 'add' | 'subtract'; preview?: BrushPreview;
  loading?: boolean; applying?: boolean; error?: string;
  onZoneIdsChange: (ids: string[]) => void; onWidth: (width: number) => void;
  onOperation: (operation: 'add' | 'subtract') => void; onPreview: (draft: BrushDraft) => void;
  onApply: () => void; onClear: () => void; onCancel: () => void;
  settings?: LiveBrushSettings; onSettingsChange?: (settings: LiveBrushSettings) => void;
}) {
  const [density, setDensity] = useState<BrushPreviewRequest['density']>(settings?.density ?? 'balanced');
  const [composition, setComposition] = useState<BrushPreviewRequest['composition']>(settings?.composition ?? 'trees');
  const [treeShare, setTreeShare] = useState((settings?.treeShare ?? .7) * 100);
  const [spacing, setSpacing] = useState(settings?.spacing ?? 6);
  const [treeSpeciesId, setTreeSpeciesId] = useState(settings?.treeSpeciesId);
  const [shrubSpeciesId, setShrubSpeciesId] = useState(settings?.shrubSpeciesId);
  const hasSpecies = !species || ((composition === 'shrubs' || Boolean(treeSpeciesId)) && (composition === 'trees' || Boolean(shrubSpeciesId)));
  const previewRef = useRef(onPreview);
  previewRef.current = onPreview;
  const disabled = Boolean(applying || !zoneIds.length);
  const additions = strokes.filter(stroke => stroke.mode === 'add').length;

  useEffect(() => { onSettingsChange?.({ density, composition, treeShare: treeShare / 100, spacing, treeSpeciesId, shrubSpeciesId }); }, [composition, density, onSettingsChange, spacing, treeShare, treeSpeciesId, shrubSpeciesId]);
  useEffect(() => {
    if (!strokes.length || !zoneIds.length || !hasSpecies && strokes.some(stroke => stroke.mode === 'add')) return;
    const timer = window.setTimeout(() => previewRef.current({ zone_ids: zoneIds, strokes, width_m: width, spacing_m: spacing, density, composition, tree_share: treeShare / 100, seed: 47, max_sites: 500, tree_species_revision_id: treeSpeciesId, shrub_species_revision_id: shrubSpeciesId, size_class: 'standard' }), 100);
    return () => window.clearTimeout(timer);
  }, [composition, density, spacing, strokes, treeShare, width, zoneIds, treeSpeciesId, shrubSpeciesId, hasSpecies]);

  return <EditorPanel title="Кисть посадок" label="Настройки кисти">
    <PlantingZonePicker zones={zones} selectedIds={zoneIds} disabled={applying} onChange={onZoneIdsChange} />
    <fieldset className="editor-fieldset" aria-label="Кисть">
      <EditorField label="Режим"><Select aria-label="Режим кисти" value={operation} disabled={disabled} onChange={event => onOperation(event.target.value as typeof operation)}><option value="add">Добавлять</option><option value="subtract">Убирать</option></Select></EditorField>
      <EditorField label="Диаметр, м"><EditorNumber label="Диаметр кисти" value={width} onChange={onWidth} min={2} max={100} step={2} disabled={disabled} /></EditorField>
    </fieldset>
    <fieldset className="editor-fieldset" aria-label="Посадки">
      <EditorField label="Состав"><Select aria-label="Состав кисти" value={composition} disabled={disabled} onChange={event => { const value = event.target.value as typeof composition; setComposition(value); setSpacing(value === 'shrubs' ? 2 : value === 'mixed' ? 4 : 6); }}><option value="trees">Деревья</option><option value="shrubs">Кустарники</option><option value="mixed">Смешанный</option></Select></EditorField>
      {species && operation === 'add' ? <>{composition !== 'shrubs' ? <EditorField label="Деревья"><SpeciesPicker label="Порода деревьев для кисти" species={species.filter(item => item.kind === 'tree')} value={treeSpeciesId} onChange={setTreeSpeciesId} disabled={applying} /></EditorField> : null}{composition !== 'trees' ? <EditorField label="Кустарники"><SpeciesPicker label="Порода кустарников для кисти" species={species.filter(item => item.kind === 'shrub')} value={shrubSpeciesId} onChange={setShrubSpeciesId} disabled={applying} /></EditorField> : null}</> : null}
      {!hasSpecies && operation === 'add' ? <p className="module-note">Выберите породу перед рисованием. Она нужна для прогноза роста.</p> : null}
      {composition === 'mixed' ? <EditorField label="Деревья, %"><EditorNumber label="Доля деревьев" value={treeShare} onChange={setTreeShare} min={0} max={100} step={10} disabled={disabled} /></EditorField> : null}
      <EditorField label="Шаг, м"><EditorNumber label="Шаг кисти" value={spacing} onChange={setSpacing} min={1} max={50} step={.5} disabled={disabled} /></EditorField>
      <EditorField label="Плотность"><Select aria-label="Плотность кисти" value={density} disabled={disabled} onChange={event => setDensity(event.target.value as typeof density)}><option value="sparse">Редкая</option><option value="balanced">Средняя</option><option value="dense">Плотная</option></Select></EditorField>
    </fieldset>
    {!zoneIds.length ? <p className="editor-panel__hint"><strong>Выберите участок</strong></p> : !strokes.length ? <p className="editor-panel__hint">Рисуйте по участку</p> : !preview || loading ? <p className="editor-panel__hint">Контуры — ещё не проверенные места</p> : null}
    {strokes.length ? <section className="editor-panel__section" aria-label="Предпросмотр кисти">
      <h3>Предпросмотр</h3>
      <div role="status">{loading ? 'Проверяем размещение…' : preview ? `К добавлению: ${preview.added_count}` : 'Раскладка ещё не проверена'}</div>
      {preview?.removed_count ? <p>К удалению: {preview.removed_count}</p> : null}
      {preview?.skipped.length ? <p className="editor-notice">Исключено проверкой: {preview.skipped.length}</p> : null}
      <EditorDisclosure title={`Мазки: ${strokes.length}`}><p>добавить {additions}, убрать {strokes.length - additions}</p><Button variant="ghost" controlSize="compact" disabled={applying} onClick={onClear}>Очистить мазки</Button></EditorDisclosure>
      <EditorActions><Button variant="ghost" controlSize="compact" disabled={applying} onClick={onCancel}>Отмена</Button><Button variant="primary" controlSize="compact" loading={applying} disabled={loading || !preview?.change_set?.can_apply} onClick={onApply}>{preview?.added_count ? `Добавить ${preview.added_count}` : preview?.removed_count ? `Убрать ${preview.removed_count}` : 'Применить'}</Button></EditorActions>
    </section> : null}
    {error ? <p className="editor-notice editor-notice--error" role="alert">{error}</p> : null}
  </EditorPanel>;
}
