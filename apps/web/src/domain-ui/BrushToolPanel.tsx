import { useEffect, useRef, useState } from 'react';
import type { BrushPreview, BrushPreviewRequest, BrushStroke, PlantingZoneAssignment } from '@green/api-client';
import { Button, FormField, InlineMessage, NumberStepper, Select } from '@green/ui';
import { InspectorHeader } from './InspectorHeader';
import { PlantingZonePicker } from './PlantingZonePicker';

type BrushDraft = Omit<BrushPreviewRequest, 'base_plan_version'>;

export function BrushToolPanel({ strokes, zones, zoneIds, width, operation, preview, loading, error, onZoneIdsChange, onWidth, onOperation, onPreview, onApply, onClear, onCancel }: {
  strokes: BrushStroke[];
  zones: PlantingZoneAssignment[];
  zoneIds: string[];
  width: number;
  operation: 'add' | 'subtract';
  preview?: BrushPreview;
  loading?: boolean;
  error?: string;
  onZoneIdsChange: (ids: string[]) => void;
  onWidth: (width: number) => void;
  onOperation: (operation: 'add' | 'subtract') => void;
  onPreview: (draft: BrushDraft) => void;
  onApply: () => void;
  onClear: () => void;
  onCancel: () => void;
}) {
  const [density, setDensity] = useState<BrushPreviewRequest['density']>('balanced');
  const [composition, setComposition] = useState<BrushPreviewRequest['composition']>('trees');
  const [treeShare, setTreeShare] = useState(70);
  const addCount = strokes.filter((stroke) => stroke.mode === 'add').length;
  const subtractCount = strokes.length - addCount;
  const spacing = composition === 'shrubs' ? 2 : composition === 'mixed' ? 4 : 6;
  const previewRef = useRef(onPreview);
  previewRef.current = onPreview;

  useEffect(() => {
    if (!strokes.length || !zoneIds.length) return;
    const timer = window.setTimeout(() => previewRef.current({ zone_ids: zoneIds, strokes, width_m: width, spacing_m: spacing, density, composition, tree_share: treeShare / 100, seed: 47, max_sites: 500 }), 220);
    return () => window.clearTimeout(timer);
  }, [composition, density, spacing, strokes, treeShare, width, zoneIds]);

  return <div className="project-inspector brush-tool-panel">
    <InspectorHeader title="Кисть посадок" meta="Массовая ручная раскладка" />
    <div className="brush-tool-panel__content">
      <section className="brush-tool-panel__guide">
        <strong>{strokes.length ? (loading ? 'Проверяем места' : `Мазков ${strokes.length}`) : 'Рисуйте по участку'}</strong>
        <span>Посадки появятся после мазка</span>
        {strokes.length ? <small>добавить {addCount}, убрать {subtractCount}</small> : null}
      </section>
      <PlantingZonePicker zones={zones} selectedIds={zoneIds} disabled={loading} onChange={onZoneIdsChange} />
      <FormField label="Режим"><Select aria-label="Режим кисти" value={operation} onChange={(event) => onOperation(event.target.value as typeof operation)}><option value="add">Добавлять посадки</option><option value="subtract">Убирать посадки</option></Select></FormField>
      <FormField label="Состав"><Select aria-label="Состав кисти" value={composition} onChange={(event) => setComposition(event.target.value as typeof composition)}><option value="trees">Деревья</option><option value="shrubs">Кустарники</option><option value="mixed">Смешанный</option></Select></FormField>
      <div className="brush-tool-panel__setting"><span>Диаметр, м</span><NumberStepper label="Диаметр кисти" value={width} onChange={onWidth} min={2} max={100} step={2} /></div>
      <FormField label="Плотность"><Select aria-label="Плотность кисти" value={density} onChange={(event) => setDensity(event.target.value as typeof density)}><option value="sparse">Редкая</option><option value="balanced">Средняя</option><option value="dense">Плотная</option></Select></FormField>
      {composition === 'mixed' ? <div className="brush-tool-panel__setting"><span>Доля деревьев, %</span><NumberStepper label="Доля деревьев" value={treeShare} onChange={setTreeShare} min={0} max={100} step={10} /></div> : null}
      {preview ? <section className="brush-preview-summary" aria-live="polite"><strong>{operation === 'subtract' ? `Будет убрано ${preview.removed_count}` : `Найдено ${preview.added_count}`}</strong>{preview.skipped.length ? <span>Часть мест исключена</span> : <span>Все места проходят проверку</span>}</section> : null}
      {strokes.length ? <Button variant="ghost" onClick={onClear}>Очистить мазки</Button> : null}
      {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    </div>
    <div className="inspector-spacer" />
    <footer><Button variant="secondary" disabled={loading} onClick={onCancel}>Готово</Button><Button variant="primary" loading={loading} disabled={!preview?.change_set} onClick={onApply}>{preview?.change_set ? (operation === 'subtract' ? `Убрать ${preview.removed_count}` : `Добавить ${preview.added_count}`) : 'Рисуйте на карте'}</Button></footer>
  </div>;
}
