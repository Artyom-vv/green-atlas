import { useEffect, useMemo, useState } from 'react';
import type { FillPatternRequest, PlantingZoneAssignment, RowPatternRequest, SpeciesRevision } from '@green/api-client';
import { Button, Checkbox, FormField, InlineMessage, NumberStepper, Select, StepProgress } from '@green/ui';

type Axis = RowPatternRequest['axis'];
type PatternDraft = Omit<RowPatternRequest, 'base_plan_version'> | Omit<FillPatternRequest, 'base_plan_version'>;

export function PatternToolPanel({ mode, zones, species, axis, selectedZoneIds = [], drawingZone = false, loading, error, resultNote, onSelectedZoneIdsChange, onDrawZone, onPreview, onCancel }: {
  mode: 'row' | 'fill';
  zones: PlantingZoneAssignment[];
  species: SpeciesRevision[];
  axis?: Axis;
  selectedZoneIds?: string[];
  drawingZone?: boolean;
  loading?: boolean;
  error?: string;
  resultNote?: string;
  onSelectedZoneIdsChange?: (ids: string[]) => void;
  onDrawZone?: () => void;
  onPreview: (draft: PatternDraft) => void;
  onCancel: () => void;
}) {
  const [plantKind, setPlantKind] = useState<'tree' | 'shrub'>('tree');
  const availableSpecies = useMemo(() => species.filter((item) => item.kind === plantKind), [plantKind, species]);
  const [speciesId, setSpeciesId] = useState<string>();
  const [targetCount, setTargetCount] = useState(40);
  const [spacing, setSpacing] = useState(6);
  const [side, setSide] = useState<'center' | 'left' | 'right' | 'both'>('center');
  const [lateralOffset, setLateralOffset] = useState(3);
  const [startOffset, setStartOffset] = useState(0);
  const [endOffset, setEndOffset] = useState(0);
  const [layout, setLayout] = useState<'staggered' | 'natural'>('natural');
  const [edgeOffset] = useState(1);
  const [angle] = useState(0);
  const [seed] = useState(47);
  useEffect(() => {
    if (!availableSpecies.some((item) => item.id === speciesId)) setSpeciesId(availableSpecies[0]?.id);
  }, [availableSpecies, speciesId]);

  const canPreview = mode === 'row' ? Boolean(axis) : selectedZoneIds.length > 0;
  const selectedZoneLabels = useMemo(() => new Set(selectedZoneIds), [selectedZoneIds]);
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
        size_class: 'standard',
        species_revision_id: speciesId,
      });
    }
    if (mode === 'fill') {
      onPreview({
        type: 'fill',
        plant_kind: plantKind,
        zone_ids: selectedZoneIds,
        placement_mode: 'count',
        target_count: targetCount,
        layout,
        spacing_m: spacing,
        edge_offset_m: edgeOffset,
        angle_deg: angle,
        seed,
        size_class: 'standard',
        species_revision_id: speciesId,
      });
    }
  };

  return <div className="project-inspector pattern-tool-panel">
    <header><span><strong>{mode === 'row' ? 'Ряд посадок' : 'Разместить посадки'}</strong><small>{mode === 'row' ? 'По выбранной линии' : 'По выбранным участкам'}</small></span></header>
    <div className="pattern-tool-panel__content">
      <StepProgress current={mode === 'fill' && !selectedZoneIds.length ? 0 : 1} steps={[{ id: 'areas', label: 'Участки' }, { id: 'placement', label: 'Посадки' }, { id: 'review', label: 'Проверка' }]} />
      {mode === 'row' ? <section className="pattern-tool-panel__axis">
        <strong>{axis ? 'Ось задана' : 'Нарисуйте ось на карте'}</strong>
        <span>{axis ? 'Можно рассчитать размещение' : 'Укажите начало и конец'}</span>
      </section> : null}
      {mode === 'fill' ? <fieldset className="pattern-tool-panel__zones">
        <legend>Участки</legend>
        {!selectedZoneIds.length ? <InlineMessage tone="info">Выберите участок на карте или обведите новый</InlineMessage> : null}
        {zones.flatMap((zone) => zone.id ? [<Checkbox key={zone.id} label={zone.label} checked={selectedZoneLabels.has(zone.id)} onChange={(event) => onSelectedZoneIdsChange?.(event.target.checked ? [...new Set([...selectedZoneIds, zone.id!])] : selectedZoneIds.filter((id) => id !== zone.id))} />] : [])}
        <Button variant="secondary" disabled={loading} onClick={drawingZone ? onCancel : onDrawZone}>{drawingZone ? 'Отменить обводку' : 'Обвести новый участок'}</Button>
      </fieldset> : null}
      {mode === 'fill' && !drawingZone ? <FormField label="Способ размещения"><Select value={layout} onChange={(event) => setLayout(event.target.value as typeof layout)}><option value="natural">Естественно</option><option value="staggered">Равномерно</option></Select></FormField> : null}
      {drawingZone ? <InlineMessage tone="info">Поставьте точки по границе участка и замкните контур</InlineMessage> : null}
      {!drawingZone ? <>
      <FormField label="Растительность"><Select value={plantKind} onChange={(event) => { const value = event.target.value as 'tree' | 'shrub'; setPlantKind(value); setSpacing(value === 'tree' ? 6 : 2); }}><option value="tree">Деревья</option><option value="shrub">Кустарники</option></Select></FormField>
      <FormField label="Порода"><Select aria-label="Порода" value={speciesId ?? ''} onChange={(event) => setSpeciesId(event.target.value)}>{availableSpecies.map((item) => <option key={item.id} value={item.id}>{item.common_name}</option>)}</Select></FormField>
      {mode === 'row' ? <FormField label="Сторона оси"><Select value={side} onChange={(event) => setSide(event.target.value as typeof side)}><option value="center">По оси</option><option value="left">Слева</option><option value="right">Справа</option><option value="both">С двух сторон</option></Select></FormField> : null}
      {mode === 'fill' ? <div className="pattern-tool-panel__setting"><span>Количество</span><NumberStepper label="Количество посадок" value={targetCount} onChange={setTargetCount} min={1} max={5000} step={10} /></div> : null}
      <div className="pattern-tool-panel__setting"><span>{mode === 'fill' ? 'Минимальный шаг, м' : 'Шаг, м'}</span><NumberStepper label="Шаг между посадками" value={spacing} onChange={setSpacing} min={plantKind === 'tree' ? 5 : 1.6} max={30} step={plantKind === 'tree' ? 0.5 : 0.2} /></div>
      {mode === 'row' && side !== 'center' ? <div className="pattern-tool-panel__setting"><span>От оси, м</span><NumberStepper label="Поперечный отступ" value={lateralOffset} onChange={setLateralOffset} min={0.5} max={30} step={0.5} /></div> : null}
      {mode === 'row' ? <><div className="pattern-tool-panel__setting"><span>От начала, м</span><NumberStepper label="Отступ от начала" value={startOffset} onChange={setStartOffset} min={0} max={100} step={0.5} /></div><div className="pattern-tool-panel__setting"><span>От конца, м</span><NumberStepper label="Отступ от конца" value={endOffset} onChange={setEndOffset} min={0} max={100} step={0.5} /></div></> : null}
      {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
      {resultNote ? <InlineMessage tone="warning">{resultNote}</InlineMessage> : null}
      </> : null}
    </div>
    <div className="inspector-spacer" />
    {!drawingZone ? <footer><Button variant="secondary" disabled={loading} onClick={onCancel}>Отмена</Button><Button variant="primary" loading={loading} disabled={!canPreview || !speciesId} onClick={submit}>Рассчитать</Button></footer> : null}
  </div>;
}
