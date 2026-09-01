import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FillPatternRequest, PatternPreview, PlantingZoneAssignment, RowPatternRequest, SpeciesRevision, SpeciesShortlistItem } from '@green/api-client';
import { Button, Checkbox, FormField, InlineMessage, NumberStepper, Select, StepProgress } from '@green/ui';
import { GrowthHorizonControl, type GrowthHorizon } from './GrowthHorizonControl';
import { forecastAt } from './growthForecast';

type Axis = RowPatternRequest['axis'];
type PatternDraft = Omit<RowPatternRequest, 'base_plan_version'> | Omit<FillPatternRequest, 'base_plan_version'>;

export function PatternToolPanel({ mode, zones, species, shortlist, shortlistLoading, axis, axisSource, selectedZoneIds = [], drawingZone = false, loading, error, resultNote, preview, growthHorizon, onGrowthHorizon, onSelectedZoneIdsChange, onDrawZone, onPreview, onApply, onCancel }: {
  mode: 'row' | 'fill';
  zones: PlantingZoneAssignment[];
  species: SpeciesRevision[];
  shortlist?: SpeciesShortlistItem[];
  shortlistLoading?: boolean;
  axis?: Axis;
  axisSource?: { type: 'dxf' | 'manual'; label: string };
  selectedZoneIds?: string[];
  drawingZone?: boolean;
  loading?: boolean;
  error?: string;
  resultNote?: string;
  preview?: PatternPreview;
  growthHorizon?: GrowthHorizon;
  onGrowthHorizon?: (year: GrowthHorizon) => void;
  onSelectedZoneIdsChange?: (ids: string[]) => void;
  onDrawZone?: () => void;
  onPreview: (draft: PatternDraft) => void;
  onApply?: () => void;
  onCancel: () => void;
}) {
  const [composition, setComposition] = useState<'trees' | 'shrubs' | 'mixed'>('trees');
  const plantKind: 'tree' | 'shrub' = composition === 'shrubs' ? 'shrub' : 'tree';
  const shortlistSpecies = useMemo(() => shortlist?.map((item) => item.species), [shortlist]);
  const availableSpecies = useMemo(() => (shortlistSpecies ?? species).filter((item) => item.kind === plantKind), [plantKind, shortlistSpecies, species]);
  const availableShrubs = useMemo(() => (shortlistSpecies ?? species).filter((item) => item.kind === 'shrub'), [shortlistSpecies, species]);
  const [speciesId, setSpeciesId] = useState<string>();
  const [shrubSpeciesId, setShrubSpeciesId] = useState<string>();
  const [targetCount, setTargetCount] = useState(40);
  const [rowPlacementMode, setRowPlacementMode] = useState<'count' | 'spacing'>('count');
  const [spacing, setSpacing] = useState(6);
  const [side, setSide] = useState<'center' | 'left' | 'right' | 'both'>('center');
  const [lateralOffset, setLateralOffset] = useState(3);
  const [startOffset, setStartOffset] = useState(0);
  const [endOffset, setEndOffset] = useState(0);
  const [layout, setLayout] = useState<'staggered' | 'natural'>('natural');
  const [spacingPolicy, setSpacingPolicy] = useState<'open' | 'balanced' | 'canopy'>('balanced');
  const [edgeOffset] = useState(1);
  const [angle] = useState(0);
  const [seed] = useState(47);
  const previewRef = useRef(onPreview);
  previewRef.current = onPreview;
  useEffect(() => {
    if (!availableSpecies.some((item) => item.id === speciesId)) setSpeciesId(availableSpecies[0]?.id);
  }, [availableSpecies, speciesId]);
  useEffect(() => {
    if (!availableShrubs.some((item) => item.id === shrubSpeciesId)) setShrubSpeciesId(availableShrubs[0]?.id);
  }, [availableShrubs, shrubSpeciesId]);

  const canPreview = selectedZoneIds.length > 0 && (mode === 'row' ? Boolean(axis) : true);
  const axisCoordinates = useMemo(() => Array.isArray(axis?.coordinates)
    ? axis.coordinates.filter((coordinate): coordinate is number[] => Array.isArray(coordinate) && coordinate.length >= 2 && coordinate.every((value) => typeof value === 'number'))
    : [], [axis]);
  const axisLength = useMemo(() => axisCoordinates.slice(1).reduce((length, coordinate, index) => {
    const previous = axisCoordinates[index];
    return length + Math.hypot(coordinate[0] - previous[0], coordinate[1] - previous[1]);
  }, 0), [axisCoordinates]);
  const selectedSpecies = availableSpecies.find((item) => item.id === speciesId);
  const shortlistById = useMemo(() => new Map(shortlist?.map((item) => [item.species.id, item]) ?? []), [shortlist]);
  const selectedZoneLabels = useMemo(() => new Set(selectedZoneIds), [selectedZoneIds]);
  const submit = useCallback(() => {
    if (mode === 'row' && axis) {
      previewRef.current({
        type: 'row',
        plant_kind: plantKind,
        zone_ids: selectedZoneIds,
        axis,
        spacing_m: spacing,
        placement_mode: rowPlacementMode,
        target_count: targetCount,
        start_offset_m: startOffset,
        end_offset_m: endOffset,
        side,
        lateral_offset_m: side === 'center' ? 0 : lateralOffset,
        size_class: 'standard',
        species_revision_id: speciesId,
        spacing_policy: spacingPolicy,
      });
    }
    if (mode === 'fill') {
      previewRef.current({
        type: 'fill',
        plant_kind: plantKind,
        composition,
        tree_share: 0.65,
        zone_ids: selectedZoneIds,
        placement_mode: 'count',
        target_count: targetCount,
        layout,
        spacing_m: spacing,
        edge_offset_m: edgeOffset,
        angle_deg: angle,
        seed,
        size_class: 'standard',
        species_revision_id: composition === 'mixed' ? undefined : speciesId,
        tree_species_revision_id: composition === 'mixed' ? speciesId : undefined,
        shrub_species_revision_id: composition === 'mixed' ? shrubSpeciesId : undefined,
        spacing_policy: spacingPolicy,
      });
    }
  }, [angle, axis, composition, edgeOffset, endOffset, lateralOffset, layout, mode, plantKind, rowPlacementMode, seed, selectedZoneIds, shrubSpeciesId, side, spacing, spacingPolicy, speciesId, startOffset, targetCount]);

  useEffect(() => {
    if (!canPreview || !speciesId || (composition === 'mixed' && !shrubSpeciesId) || drawingZone) return;
    const timer = window.setTimeout(submit, 420);
    return () => window.clearTimeout(timer);
  }, [canPreview, composition, drawingZone, shrubSpeciesId, speciesId, submit]);

  return <div className="project-inspector pattern-tool-panel">
    <header><span><strong>{mode === 'row' ? 'Ряд посадок' : 'Разместить посадки'}</strong><small>{mode === 'row' ? 'По выбранной линии' : 'По выбранным участкам'}</small></span></header>
    <div className="pattern-tool-panel__content">
      <StepProgress current={preview ? 2 : !selectedZoneIds.length ? 0 : 1} steps={[{ id: 'areas', label: 'Участки' }, { id: 'placement', label: 'Посадки' }, { id: 'review', label: 'Проверка' }]} />
      {preview ? <section className="pattern-result-review" aria-label="Результат расчёта">
        <section className="pattern-live-summary" aria-live="polite"><strong>Черновик на карте</strong><span>{preview.accepted_count} из {preview.requested_count} допустимы</span>{mode === 'fill' && preview.effective_spacing_m ? <small>Расчётный шаг {preview.effective_spacing_m} м</small> : null}</section>
        <dl className="pattern-funnel" role="group" aria-label="Воронка расчёта"><div><dt>Запрошено</dt><dd>{preview.requested_count}</dd></div><div><dt>Сгенерировано</dt><dd>{preview.generated_count ?? 0}</dd></div><div><dt>Допустимо</dt><dd>{preview.accepted_count}</dd></div><div><dt>Отклонено</dt><dd>{preview.rejected_count ?? preview.skipped.length}</dd></div><div><dt>Не вместилось</dt><dd>{preview.capacity_shortfall ?? 0}</dd></div></dl>
        {resultNote ? <InlineMessage tone="warning">{resultNote}</InlineMessage> : null}
        {preview.data_confidence && preview.data_confidence !== 'verified' ? <InlineMessage tone={preview.data_confidence === 'blocked' ? 'error' : 'warning'} title="Достоверность проверки ограничена">{preview.data_confidence_reasons?.join('; ') || 'Часть исходных ограничений не подтверждена'}</InlineMessage> : null}
        {preview.reason_summary?.length ? <InlineMessage tone="info" title="Почему позиции исключены"><ul>{preview.reason_summary.map((item) => <li key={`${item.status}:${item.code}`}>{item.count} — {item.message}</li>)}</ul></InlineMessage> : null}
        {preview.change_set && onGrowthHorizon ? <GrowthHorizonControl value={growthHorizon} forecasts={selectedSpecies ? [selectedSpecies] : []} onChange={onGrowthHorizon} /> : null}
      </section> : null}
      <fieldset className="pattern-tool-panel__zones">
        <legend>Участки</legend>
        {!selectedZoneIds.length ? <InlineMessage tone="info">Выберите рабочий участок</InlineMessage> : null}
        {zones.flatMap((zone) => zone.id ? [<Checkbox key={zone.id} label={zone.label} checked={selectedZoneLabels.has(zone.id)} onChange={(event) => onSelectedZoneIdsChange?.(event.target.checked ? [...new Set([...selectedZoneIds, zone.id!])] : selectedZoneIds.filter((id) => id !== zone.id))} />] : [])}
        {mode === 'fill' ? <Button variant="secondary" disabled={loading} onClick={drawingZone ? onCancel : onDrawZone}>{drawingZone ? 'Отменить обводку' : 'Обвести новый участок'}</Button> : null}
      </fieldset>
      {mode === 'row' ? <section className="pattern-tool-panel__axis">
        <strong>{axis ? 'Линия выбрана' : 'Выберите линию на карте'}</strong>
        {axis ? <dl><dt>Источник</dt><dd>{axisSource?.label ?? 'Линия DXF'}</dd><dt>Длина</dt><dd>{axisLength?.toFixed(1)} м</dd></dl> : <span>Кликните по линии DXF. Shift — нарисовать свою ось</span>}
      </section> : null}
      {mode === 'fill' && canPreview && !drawingZone ? <FormField label="Способ размещения"><Select value={layout} onChange={(event) => setLayout(event.target.value as typeof layout)}><option value="natural">Естественно</option><option value="staggered">Равномерно</option></Select></FormField> : null}
      {drawingZone ? <InlineMessage tone="info">Поставьте точки по границе участка и замкните контур</InlineMessage> : null}
      {!drawingZone && canPreview ? <>
      <FormField label="Состав группы"><Select aria-label="Состав группы" value={composition} onChange={(event) => { const value = event.target.value as typeof composition; setComposition(value); setSpacing(value === 'shrubs' ? 2 : 6); }}><option value="trees">Деревья</option><option value="shrubs">Кустарники</option>{mode === 'fill' ? <option value="mixed">Деревья и кустарники</option> : null}</Select></FormField>
      <fieldset className="species-choice"><legend>Порода для участка</legend>{shortlistLoading ? <span>Подбираем по доступным данным</span> : availableSpecies.map((item) => { const forecast = forecastAt(item.canopy_forecast, growthHorizon ?? 20); const recommendation = shortlistById.get(item.id); const rootLabel = item.root_architecture === 'shallow' ? 'поверхностные' : item.root_architecture === 'deep' ? 'глубокие' : item.root_architecture === 'mixed' ? 'смешанные' : 'не подтверждены'; return <button type="button" key={item.id} className={speciesId === item.id ? 'species-choice__item is-selected' : 'species-choice__item'} aria-label={`${item.common_name}. ${recommendation?.status === 'review' ? 'Нужна проверка' : 'Предварительно подходит'}`} title={recommendation?.reasons?.join('. ')} onClick={() => setSpeciesId(item.id)}><span><strong>{item.common_name}</strong><small>{item.crown_shape === 'spreading' ? 'раскидистая' : item.crown_shape === 'conical' ? 'коническая' : item.crown_shape === 'oval' ? 'овальная' : 'компактная'} крона</small><small>Корни: {rootLabel}</small>{recommendation?.estimated_capacity != null ? <small>Оценка: до {recommendation.estimated_capacity} на участке</small> : null}</span><span className="species-choice__measure"><b>{forecast ? `${(forecast.radius_min_m * 2).toFixed(1)}–${(forecast.radius_max_m * 2).toFixed(1)} м` : 'нет данных'}</b><small>{recommendation?.status === 'review' ? 'Нужна проверка' : 'Предварительно подходит'}</small></span></button>; })}</fieldset>
      {composition === 'mixed' ? <FormField label="Порода кустарника"><Select aria-label="Порода кустарника" value={shrubSpeciesId} onChange={(event) => setShrubSpeciesId(event.target.value)}>{availableShrubs.map((item) => <option key={item.id} value={item.id}>{item.common_name}</option>)}</Select></FormField> : null}
      <FormField label="Плотность группы"><Select value={spacingPolicy} onChange={(event) => setSpacingPolicy(event.target.value as typeof spacingPolicy)}><option value="canopy">Плотно, кроны сомкнутся</option><option value="balanced">Естественно</option><option value="open">Свободно</option></Select></FormField>
      {mode === 'row' ? <FormField label="Сторона оси"><Select value={side} onChange={(event) => setSide(event.target.value as typeof side)}><option value="center">По оси</option><option value="left">Слева</option><option value="right">Справа</option><option value="both">С двух сторон</option></Select></FormField> : null}
      {mode === 'row' ? <FormField label="Задать ряд"><Select value={rowPlacementMode} onChange={(event) => setRowPlacementMode(event.target.value as typeof rowPlacementMode)}><option value="count">По количеству</option><option value="spacing">По шагу</option></Select></FormField> : null}
      {mode === 'row' && rowPlacementMode === 'count' ? <div className="pattern-tool-panel__setting"><span>Количество</span><NumberStepper label="Количество посадок" value={targetCount} onChange={setTargetCount} min={2} max={5000} step={10} /></div> : null}
      {mode === 'fill' ? <div className="pattern-tool-panel__setting"><span>Количество</span><NumberStepper label="Количество посадок" value={targetCount} onChange={setTargetCount} min={1} max={5000} step={10} /></div> : null}
      {mode === 'row' && rowPlacementMode === 'spacing' ? <div className="pattern-tool-panel__setting"><span>Шаг, м</span><NumberStepper label="Шаг между посадками" value={spacing} onChange={setSpacing} min={plantKind === 'tree' ? 5 : 1.6} max={30} step={plantKind === 'tree' ? 0.5 : 0.2} /></div> : null}
      {mode === 'row' && side !== 'center' ? <div className="pattern-tool-panel__setting"><span>От оси, м</span><NumberStepper label="Поперечный отступ" value={lateralOffset} onChange={setLateralOffset} min={0.5} max={30} step={0.5} /></div> : null}
      {mode === 'row' ? <><div className="pattern-tool-panel__setting"><span>От начала, м</span><NumberStepper label="Отступ от начала" value={startOffset} onChange={setStartOffset} min={0} max={100} step={0.5} /></div><div className="pattern-tool-panel__setting"><span>От конца, м</span><NumberStepper label="Отступ от конца" value={endOffset} onChange={setEndOffset} min={0} max={100} step={0.5} /></div></> : null}
      {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
      </> : null}
    </div>
    <div className="inspector-spacer" />
    {!drawingZone ? <footer><Button variant="secondary" disabled={loading} onClick={onCancel}>Отмена</Button><Button variant="primary" loading={loading} disabled={!canPreview || !speciesId || (composition === 'mixed' && !shrubSpeciesId) || Boolean(preview && !preview.change_set)} onClick={preview?.change_set ? onApply : submit}>{!canPreview ? (mode === 'row' ? 'Выберите линию' : 'Выберите участок') : preview?.change_set ? `Добавить ${preview.accepted_count}` : 'Показать'}</Button></footer> : null}
  </div>;
}
