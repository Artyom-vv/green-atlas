import { useState } from 'react';
import type { BrushPreviewRequest, BrushStroke } from '@green/api-client';
import { Button, FormField, InlineMessage, NumberStepper, Select } from '@green/ui';

type BrushDraft = Omit<BrushPreviewRequest, 'base_plan_version'>;

export function BrushToolPanel({ strokes, zoneIds, loading, error, resultNote, onPreview, onClear, onCancel }: {
  strokes: BrushStroke[];
  zoneIds: string[];
  loading?: boolean;
  error?: string;
  resultNote?: string;
  onPreview: (draft: BrushDraft) => void;
  onClear: () => void;
  onCancel: () => void;
}) {
  const [width, setWidth] = useState(12);
  const [spacing, setSpacing] = useState(6);
  const [density, setDensity] = useState<BrushPreviewRequest['density']>('balanced');
  const [composition, setComposition] = useState<BrushPreviewRequest['composition']>('trees');
  const [treeShare, setTreeShare] = useState(70);
  const [seed, setSeed] = useState(47);
  const addCount = strokes.filter((stroke) => stroke.mode === 'add').length;
  const subtractCount = strokes.length - addCount;

  return <div className="project-inspector brush-tool-panel">
    <header><span><strong>Кисть посадок</strong><small>Массовая ручная раскладка</small></span></header>
    <div className="brush-tool-panel__content">
      <section className="brush-tool-panel__guide">
        <strong>{strokes.length ? `${strokes.length} мазка в черновике` : 'Проведите по карте'}</strong>
        <span>Shift добавит мазок. Alt вычтет посадки. Esc отменит текущую линию.</span>
        {strokes.length ? <small>Добавление: {addCount}. Вычитание: {subtractCount}.</small> : null}
      </section>
      <FormField label="Состав"><Select aria-label="Состав кисти" value={composition} onChange={(event) => setComposition(event.target.value as typeof composition)}><option value="trees">Деревья</option><option value="shrubs">Кустарники</option><option value="mixed">Смешанный</option></Select></FormField>
      {composition === 'mixed' ? <div className="brush-tool-panel__setting"><span>Доля деревьев, %</span><NumberStepper label="Доля деревьев" value={treeShare} onChange={setTreeShare} min={0} max={100} step={10} /></div> : null}
      <FormField label="Плотность"><Select aria-label="Плотность кисти" value={density} onChange={(event) => setDensity(event.target.value as typeof density)}><option value="sparse">Редкая</option><option value="balanced">Средняя</option><option value="dense">Плотная</option></Select></FormField>
      <div className="brush-tool-panel__setting"><span>Ширина, м</span><NumberStepper label="Ширина кисти" value={width} onChange={setWidth} min={1} max={100} step={1} /></div>
      <div className="brush-tool-panel__setting"><span>Мин. шаг, м</span><NumberStepper label="Минимальный шаг" value={spacing} onChange={setSpacing} min={1} max={30} step={0.5} /></div>
      <div className="brush-tool-panel__setting"><span>Вариант</span><NumberStepper label="Вариант расположения" value={seed} onChange={setSeed} min={0} max={999} /></div>
      {strokes.length ? <Button variant="ghost" onClick={onClear}>Очистить мазки</Button> : null}
      {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
      {resultNote ? <InlineMessage tone="warning">{resultNote}</InlineMessage> : null}
    </div>
    <div className="inspector-spacer" />
    <footer><Button variant="secondary" disabled={loading} onClick={onCancel}>Отмена</Button><Button variant="primary" loading={loading} disabled={!strokes.length || !zoneIds.length} onClick={() => onPreview({ zone_ids: zoneIds, strokes, width_m: width, spacing_m: spacing, density, composition, tree_share: treeShare / 100, seed, max_sites: 500 })}>Показать</Button></footer>
  </div>;
}
