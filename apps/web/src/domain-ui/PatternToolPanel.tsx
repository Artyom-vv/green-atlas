import { useEffect, useMemo, useState } from 'react';
import type { FillPatternRequest, PlantingZoneAssignment, RowPatternRequest } from '@green/api-client';
import { Button, Checkbox, FormField, InlineMessage, NumberStepper, Select } from '@green/ui';

type Axis = RowPatternRequest['axis'];
type PatternDraft = Omit<RowPatternRequest, 'base_plan_version'> | Omit<FillPatternRequest, 'base_plan_version'>;

export function PatternToolPanel({ mode, zones, axis, loading, error, resultNote, onPreview, onCancel }: {
  mode: 'row' | 'fill';
  zones: PlantingZoneAssignment[];
  axis?: Axis;
  loading?: boolean;
  error?: string;
  resultNote?: string;
  onPreview: (draft: PatternDraft) => void;
  onCancel: () => void;
}) {
  const [plantKind, setPlantKind] = useState<'tree' | 'shrub'>('tree');
  const [spacing, setSpacing] = useState(6);
  const [side, setSide] = useState<'center' | 'left' | 'right' | 'both'>('center');
  const [lateralOffset, setLateralOffset] = useState(3);
  const [startOffset, setStartOffset] = useState(0);
  const [endOffset, setEndOffset] = useState(0);
  const [layout, setLayout] = useState<'regular' | 'staggered' | 'natural'>('staggered');
  const [edgeOffset, setEdgeOffset] = useState(1);
  const [angle, setAngle] = useState(0);
  const [seed, setSeed] = useState(47);
  const zoneIdList = (items: PlantingZoneAssignment[]) => items.flatMap((zone) => zone.id ? [zone.id] : []);
  const [zoneIds, setZoneIds] = useState<string[]>(() => zoneIdList(zones));

  useEffect(() => {
    const available = new Set(zoneIdList(zones));
    setZoneIds((current) => {
      const retained = current.filter((id) => available.has(id));
      return retained.length ? retained : zoneIdList(zones);
    });
  }, [zones]);

  const canPreview = mode === 'row' ? Boolean(axis) : zoneIds.length > 0;
  const selectedZoneLabels = useMemo(() => new Set(zoneIds), [zoneIds]);
  const submit = () => {
    if (mode === 'row' && axis) {
      onPreview({
        type: 'row',
        plant_kind: plantKind,
        axis,
        spacing_m: spacing,
        start_offset_m: startOffset,
        end_offset_m: endOffset,
        side,
        lateral_offset_m: side === 'center' ? 0 : lateralOffset,
        size_class: 'unspecified',
      });
    }
    if (mode === 'fill') {
      onPreview({
        type: 'fill',
        plant_kind: plantKind,
        zone_ids: zoneIds,
        layout,
        spacing_m: spacing,
        edge_offset_m: edgeOffset,
        angle_deg: angle,
        seed,
        size_class: 'unspecified',
      });
    }
  };

  return <div className="project-inspector pattern-tool-panel">
    <header><span><strong>{mode === 'row' ? 'Ряд посадок' : 'Заполнение участков'}</strong><small>{mode === 'row' ? 'Ось, шаг и стороны' : 'Несколько зон за один раз'}</small></span></header>
    <div className="pattern-tool-panel__content">
      {mode === 'row' ? <section className="pattern-tool-panel__axis">
        <strong>{axis ? 'Ось задана' : 'Нарисуйте ось на карте'}</strong>
        <span>{axis ? 'Новая линия заменит текущую.' : 'Кликните начальную и конечную точки.'}</span>
      </section> : null}
      {mode === 'fill' ? <fieldset className="pattern-tool-panel__zones">
        <legend>Участки</legend>
        {zones.flatMap((zone) => zone.id ? [<Checkbox key={zone.id} label={zone.label} checked={selectedZoneLabels.has(zone.id)} onChange={(event) => setZoneIds((current) => event.target.checked ? [...new Set([...current, zone.id!])] : current.filter((id) => id !== zone.id))} />] : [])}
      </fieldset> : null}
      <FormField label="Растительность"><Select value={plantKind} onChange={(event) => { const value = event.target.value as 'tree' | 'shrub'; setPlantKind(value); setSpacing(value === 'tree' ? 6 : 2); }}><option value="tree">Деревья</option><option value="shrub">Кустарники</option></Select></FormField>
      {mode === 'fill' ? <FormField label="Рисунок"><Select value={layout} onChange={(event) => setLayout(event.target.value as typeof layout)}><option value="regular">Регулярный</option><option value="staggered">Шахматный</option><option value="natural">Естественный</option></Select></FormField> : null}
      {mode === 'row' ? <FormField label="Сторона оси"><Select value={side} onChange={(event) => setSide(event.target.value as typeof side)}><option value="center">По оси</option><option value="left">Слева</option><option value="right">Справа</option><option value="both">С двух сторон</option></Select></FormField> : null}
      <div className="pattern-tool-panel__setting"><span>Шаг, м</span><NumberStepper label="Шаг между посадками" value={spacing} onChange={setSpacing} min={plantKind === 'tree' ? 5 : 1.6} max={30} step={plantKind === 'tree' ? 0.5 : 0.2} /></div>
      {mode === 'row' && side !== 'center' ? <div className="pattern-tool-panel__setting"><span>От оси, м</span><NumberStepper label="Поперечный отступ" value={lateralOffset} onChange={setLateralOffset} min={0.5} max={30} step={0.5} /></div> : null}
      {mode === 'row' ? <><div className="pattern-tool-panel__setting"><span>От начала, м</span><NumberStepper label="Отступ от начала" value={startOffset} onChange={setStartOffset} min={0} max={100} step={0.5} /></div><div className="pattern-tool-panel__setting"><span>От конца, м</span><NumberStepper label="Отступ от конца" value={endOffset} onChange={setEndOffset} min={0} max={100} step={0.5} /></div></> : null}
      {mode === 'fill' ? <><div className="pattern-tool-panel__setting"><span>От края, м</span><NumberStepper label="Отступ от края" value={edgeOffset} onChange={setEdgeOffset} min={0} max={20} step={0.5} /></div><div className="pattern-tool-panel__setting"><span>Угол, °</span><NumberStepper label="Угол рисунка" value={angle} onChange={setAngle} min={-180} max={180} step={5} /></div>{layout === 'natural' ? <div className="pattern-tool-panel__setting"><span>Вариант</span><NumberStepper label="Вариант естественного рисунка" value={seed} onChange={setSeed} min={0} max={999} /></div> : null}</> : null}
      {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
      {resultNote ? <InlineMessage tone="warning">{resultNote}</InlineMessage> : null}
    </div>
    <div className="inspector-spacer" />
    <footer><Button variant="secondary" disabled={loading} onClick={onCancel}>Отмена</Button><Button variant="primary" loading={loading} disabled={!canPreview} onClick={submit}>Показать</Button></footer>
  </div>;
}
