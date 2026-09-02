import { useEffect, useMemo, useState } from 'react';
import type { FillPatternRequest, PatternPreview, PlantingZoneAssignment, RowPatternRequest, SpeciesRevision, SpeciesShortlistItem } from '@green/api-client';
import { Button, Checkbox, FormField, InlineMessage, NumberStepper, Select, StepProgress } from '@green/ui';
import { GrowthHorizonControl, type GrowthHorizon } from './GrowthHorizonControl';
import { forecastAt } from './growthForecast';
import { sourceLayerLabel } from './sourceLabels';

type Axis = RowPatternRequest['axis'];
type PatternDraft = Omit<RowPatternRequest, 'base_plan_version'> | Omit<FillPatternRequest, 'base_plan_version'>;

export function PatternToolPanel({ mode, zones, species, shortlist, shortlistLoading, axis, axisSource, selectedZoneIds = [], drawingZone = false, loading, error, preview, growthHorizon, onGrowthHorizon, onSelectedZoneIdsChange, onDrawZone, onPreview, onApply, onResetPreview, onCancel }: {
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
  preview?: PatternPreview;
  growthHorizon?: GrowthHorizon;
  onGrowthHorizon?: (year: GrowthHorizon) => void;
  onSelectedZoneIdsChange?: (ids: string[]) => void;
  onDrawZone?: () => void;
  onPreview: (draft: PatternDraft) => void;
  onApply?: () => void;
  onResetPreview?: () => void;
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
  const [fillVolume, setFillVolume] = useState<'recommended' | 'exact'>('recommended');
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
  const selectedZoneLabels = useMemo(() => new Set(selectedZoneIds), [selectedZoneIds]);
  // The shortlist capacity is only an area estimate. It cannot see every
  // pairwise conflict and must never be presented as a placement promise.
  // Keep the first calculation intentionally bounded, then report the exact
  // accepted count returned by the constraint engine.
  const recommendedCount = Math.min(40, targetCount);
  const requestedCount = mode === 'fill' && fillVolume === 'recommended' ? recommendedCount : targetCount;
  const compactAlternative = useMemo(() => {
    if (!speciesId) return undefined;
    const current = availableSpecies.find((item) => item.id === speciesId);
    if (!current) return undefined;
    return availableSpecies
      .filter((item) => item.id !== current.id && item.mature_crown_diameter_max_m < current.mature_crown_diameter_max_m)
      .sort((left, right) => left.mature_crown_diameter_max_m - right.mature_crown_diameter_max_m)[0];
  }, [availableSpecies, speciesId]);
  const duplicateZoneLabels = useMemo(() => zones.reduce<Record<string, number>>((counts, zone) => ({ ...counts, [zone.label]: (counts[zone.label] ?? 0) + 1 }), {}), [zones]);
  const submit = () => {
    if (mode === 'row' && axis) {
      onPreview({
        type: 'row',
        plant_kind: plantKind,
        zone_ids: selectedZoneIds,
        axis,
        spacing_m: spacing,
        placement_mode: rowPlacementMode,
        target_count: requestedCount,
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
      onPreview({
        type: 'fill',
        plant_kind: plantKind,
        composition,
        tree_share: 0.65,
        zone_ids: selectedZoneIds,
        placement_mode: 'count',
        target_count: requestedCount,
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
  };

  return <div className="project-inspector pattern-tool-panel">
    <header><span><strong>{mode === 'row' ? 'Ряд посадок' : 'Разместить посадки'}</strong><small>{mode === 'row' ? 'По выбранной линии' : 'По выбранным участкам'}</small></span></header>
    <div className="pattern-tool-panel__content">
      <StepProgress current={preview ? 2 : !selectedZoneIds.length ? 0 : 1} steps={[{ id: 'areas', label: 'Участки' }, { id: 'placement', label: 'Посадки' }, { id: 'review', label: 'Проверка' }]} />
      {preview ? <section className="pattern-result-review" aria-label="Результат расчёта">
        <section className="pattern-live-summary" aria-live="polite"><span><strong>{preview.accepted_count ? `Найдено ${preview.accepted_count}` : 'Мест не найдено'}</strong><small>{preview.accepted_count === preview.requested_count ? 'Все позиции проходят проверку' : `Проверено до ${preview.requested_count}`}</small></span>{mode === 'fill' && preview.effective_spacing_m ? <b>шаг {preview.effective_spacing_m} м</b> : null}</section>
        {preview.reason_summary?.length ? <details className="pattern-review-details"><summary>Почему меньше</summary><ul>{preview.reason_summary.slice(0, 4).map((item) => <li key={`${item.status}:${item.code}`}>{item.count} — {item.message}</li>)}</ul></details> : null}
        {!preview.accepted_count && compactAlternative ? <section className="pattern-next-action"><span><strong>Попробуйте компактную породу</strong><small>Её прогнозная крона занимает меньше места</small></span><Button variant="secondary" onClick={() => { setSpeciesId(compactAlternative.id); onResetPreview?.(); }}>Выбрать {compactAlternative.common_name}</Button></section> : null}
        {preview.change_set && onGrowthHorizon ? <GrowthHorizonControl value={growthHorizon} forecasts={selectedSpecies ? [selectedSpecies] : []} onChange={onGrowthHorizon} /> : null}
      </section> : <>
      <fieldset className="pattern-tool-panel__zones">
        <legend>Участки</legend>
        {!selectedZoneIds.length ? <InlineMessage tone="info">Выберите рабочий участок</InlineMessage> : null}
        {zones.flatMap((zone, index) => zone.id ? [<Checkbox key={zone.id} label={duplicateZoneLabels[zone.label] > 1 ? `${zone.label} ${index + 1}` : zone.label} checked={selectedZoneLabels.has(zone.id)} onChange={(event) => onSelectedZoneIdsChange?.(event.target.checked ? [...new Set([...selectedZoneIds, zone.id!])] : selectedZoneIds.filter((id) => id !== zone.id))} />] : [])}
        {mode === 'fill' ? <Button variant="secondary" disabled={loading} onClick={drawingZone ? onCancel : onDrawZone}>{drawingZone ? 'Отменить обводку' : 'Обвести новый участок'}</Button> : null}
      </fieldset>
      {mode === 'row' ? <section className="pattern-tool-panel__axis">
        <strong>{axis ? 'Линия выбрана' : 'Выберите линию на карте'}</strong>
        {axis ? <dl><dt>Источник</dt><dd>{sourceLayerLabel(axisSource?.label)}</dd><dt>Длина</dt><dd>{axisLength?.toFixed(1)} м</dd></dl> : <span>Кликните по линии DXF. Shift — нарисовать свою ось</span>}
      </section> : null}
      {mode === 'fill' && canPreview && !drawingZone ? <FormField label="Схема"><Select aria-label="Схема размещения" value={layout} onChange={(event) => setLayout(event.target.value as typeof layout)}><option value="natural">Свободная без рядов</option><option value="staggered">Регулярными рядами</option></Select></FormField> : null}
      {drawingZone ? <InlineMessage tone="info">Поставьте точки по границе участка и замкните контур</InlineMessage> : null}
      {!drawingZone && canPreview ? <>
      <FormField label="Состав группы"><Select aria-label="Состав группы" value={composition} onChange={(event) => { const value = event.target.value as typeof composition; setComposition(value); setSpacing(value === 'shrubs' ? 2 : 6); }}><option value="trees">Деревья</option><option value="shrubs">Кустарники</option>{mode === 'fill' ? <option value="mixed">Деревья и кустарники</option> : null}</Select></FormField>
      <FormField label="Порода"><Select aria-label="Порода для участка" value={speciesId} disabled={shortlistLoading} onChange={(event) => setSpeciesId(event.target.value)}>{availableSpecies.map((item) => <option key={item.id} value={item.id}>{item.common_name}</option>)}</Select></FormField>
      {selectedSpecies ? (() => { const forecast = forecastAt(selectedSpecies.canopy_forecast, growthHorizon ?? 20); const rootLabel = selectedSpecies.root_architecture === 'shallow' ? 'поверхностные' : selectedSpecies.root_architecture === 'deep' ? 'глубокие' : selectedSpecies.root_architecture === 'mixed' ? 'смешанные' : 'не подтверждены'; const crownClass = selectedSpecies.mature_crown_diameter_max_m <= 5 ? 'компактная крона' : selectedSpecies.mature_crown_diameter_max_m <= 8 ? 'средняя крона' : 'широкая крона'; return <section className="species-preview-card"><span><strong>{selectedSpecies.common_name}</strong><small>{rootLabel} корни</small></span><span><b>{forecast ? `${(forecast.radius_min_m * 2).toFixed(1)}–${(forecast.radius_max_m * 2).toFixed(1)} м` : 'нет прогноза'}</b><small>{crownClass}</small></span></section>; })() : null}
      {selectedSpecies && onGrowthHorizon ? <GrowthHorizonControl value={growthHorizon} forecasts={[selectedSpecies]} onChange={onGrowthHorizon} /> : null}
      {composition === 'mixed' ? <FormField label="Порода кустарника"><Select aria-label="Порода кустарника" value={shrubSpeciesId} onChange={(event) => setShrubSpeciesId(event.target.value)}>{availableShrubs.map((item) => <option key={item.id} value={item.id}>{item.common_name}</option>)}</Select></FormField> : null}
      <FormField label="Плотность группы"><Select value={spacingPolicy} onChange={(event) => setSpacingPolicy(event.target.value as typeof spacingPolicy)}><option value="canopy">Плотно, кроны сомкнутся</option><option value="balanced">Естественно</option><option value="open">Свободно</option></Select></FormField>
      {mode === 'row' ? <FormField label="Сторона оси"><Select value={side} onChange={(event) => setSide(event.target.value as typeof side)}><option value="center">По оси</option><option value="left">Слева</option><option value="right">Справа</option><option value="both">С двух сторон</option></Select></FormField> : null}
      {mode === 'row' ? <FormField label="Задать ряд"><Select value={rowPlacementMode} onChange={(event) => setRowPlacementMode(event.target.value as typeof rowPlacementMode)}><option value="count">По количеству</option><option value="spacing">По шагу</option></Select></FormField> : null}
      {mode === 'row' && rowPlacementMode === 'count' ? <div className="pattern-tool-panel__setting"><span>Количество</span><NumberStepper label="Количество посадок" value={targetCount} onChange={setTargetCount} min={2} max={5000} step={10} /></div> : null}
      {mode === 'fill' ? <FormField label="Объём"><Select aria-label="Объём посадок" value={fillVolume} onChange={(event) => setFillVolume(event.target.value as typeof fillVolume)}><option value="recommended">Подобрать количество</option><option value="exact">Задать количество</option></Select></FormField> : null}
      {mode === 'fill' && fillVolume === 'recommended' ? <p className="pattern-capacity-note">Проверим до {recommendedCount} мест и покажем результат</p> : null}
      {mode === 'fill' && fillVolume === 'exact' ? <div className="pattern-tool-panel__setting"><span>Количество</span><NumberStepper label="Количество посадок" value={targetCount} onChange={setTargetCount} min={1} max={5000} step={10} /></div> : null}
      {mode === 'row' && rowPlacementMode === 'spacing' ? <div className="pattern-tool-panel__setting"><span>Шаг, м</span><NumberStepper label="Шаг между посадками" value={spacing} onChange={setSpacing} min={plantKind === 'tree' ? 5 : 1.6} max={30} step={plantKind === 'tree' ? 0.5 : 0.2} /></div> : null}
      {mode === 'row' && side !== 'center' ? <div className="pattern-tool-panel__setting"><span>От оси, м</span><NumberStepper label="Поперечный отступ" value={lateralOffset} onChange={setLateralOffset} min={0.5} max={30} step={0.5} /></div> : null}
      {mode === 'row' ? <><div className="pattern-tool-panel__setting"><span>От начала, м</span><NumberStepper label="Отступ от начала" value={startOffset} onChange={setStartOffset} min={0} max={100} step={0.5} /></div><div className="pattern-tool-panel__setting"><span>От конца, м</span><NumberStepper label="Отступ от конца" value={endOffset} onChange={setEndOffset} min={0} max={100} step={0.5} /></div></> : null}
      {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
      </> : null}
      </>}
    </div>
    <div className="inspector-spacer" />
    {!drawingZone ? <footer>{preview?.change_set ? <><Button variant="secondary" disabled={loading} onClick={onResetPreview}>Изменить</Button><Button variant="primary" loading={loading} onClick={onApply}>{`Добавить ${preview.accepted_count}`}</Button></> : preview ? <Button variant="primary" disabled={loading} onClick={onResetPreview ?? onCancel}>Изменить условия</Button> : <><Button variant="secondary" disabled={loading} onClick={onCancel}>Отмена</Button><Button variant="primary" loading={loading} disabled={!canPreview || !speciesId || (composition === 'mixed' && !shrubSpeciesId)} onClick={submit}>{!selectedZoneIds.length ? 'Выберите участок' : mode === 'row' && !axis ? 'Выберите линию' : 'Проверить места'}</Button></>}</footer> : null}
  </div>;
}
