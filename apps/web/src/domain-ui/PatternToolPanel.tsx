import { useEffect, useMemo, useState } from 'react';
import type { FillPatternRequest, PatternPreview, PlacementMaskPreset, PlacementMaskRequest, PlantingZoneAssignment, RowPatternRequest, SpeciesRevision, SpeciesShortlistItem } from '@green/api-client';
import { Button, Disclosure, FormField, HelpDisclosure, InlineMessage, NumberStepper, Select, StepProgress } from '@green/ui';
import { GrowthHorizonControl, type GrowthHorizon } from './GrowthHorizonControl';
import { forecastAt } from './growthForecast';
import { sourceLayerLabel } from './sourceLabels';
import { InspectorHeader } from './InspectorHeader';
import { InspectorFooter, InspectorSettingRow } from './InspectorLayout';
import { PlantingZonePicker } from './PlantingZonePicker';
import { PlacementScenarioPicker, type PlacementScenarioId } from './PlacementScenarioPicker';
import { PLANTING_WORKFLOW_STEPS } from './plantingWorkflow';

type Axis = RowPatternRequest['axis'];
type PatternDraft = Omit<RowPatternRequest, 'base_plan_version'> | Omit<FillPatternRequest, 'base_plan_version'> | Omit<PlacementMaskRequest, 'base_plan_version'>;

export function PatternToolPanel({ mode, zones, species, shortlist, shortlistLoading, placementMasks, axis, axisSource, selectedZoneIds = [], drawingZone = false, loading, error, preview, growthHorizon, onGrowthHorizon, onSelectedZoneIdsChange, onDrawZone, onPreview, onApply, onResetPreview, onCancel }: {
  mode: 'row' | 'fill';
  zones: PlantingZoneAssignment[];
  species: SpeciesRevision[];
  shortlist?: SpeciesShortlistItem[];
  shortlistLoading?: boolean;
  placementMasks?: PlacementMaskPreset[];
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
  const [rowPlacementMode, setRowPlacementMode] = useState<'count' | 'spacing'>('count');
  const [spacing, setSpacing] = useState(6);
  const [side, setSide] = useState<'center' | 'left' | 'right' | 'both'>('center');
  const [lateralOffset, setLateralOffset] = useState(3);
  const [startOffset, setStartOffset] = useState(0);
  const [endOffset, setEndOffset] = useState(0);
  const [placementScenario, setPlacementScenario] = useState<PlacementScenarioId>('natural');
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
  useEffect(() => {
    if (placementScenario === 'natural' || !placementMasks) return;
    const preset = placementMasks.find((item) => item.id === placementScenario);
    if (!preset?.available) setPlacementScenario('natural');
  }, [placementMasks, placementScenario]);

  const canPreview = selectedZoneIds.length > 0 && (mode === 'row' ? Boolean(axis) : true);
  const axisCoordinates = useMemo(() => Array.isArray(axis?.coordinates)
    ? axis.coordinates.filter((coordinate): coordinate is number[] => Array.isArray(coordinate) && coordinate.length >= 2 && coordinate.every((value) => typeof value === 'number'))
    : [], [axis]);
  const axisLength = useMemo(() => axisCoordinates.slice(1).reduce((length, coordinate, index) => {
    const previous = axisCoordinates[index];
    return length + Math.hypot(coordinate[0] - previous[0], coordinate[1] - previous[1]);
  }, 0), [axisCoordinates]);
  const selectedSpecies = availableSpecies.find((item) => item.id === speciesId);
  const requestedCount = targetCount;
  const compactAlternative = useMemo(() => {
    if (!speciesId) return undefined;
    const current = availableSpecies.find((item) => item.id === speciesId);
    if (!current) return undefined;
    return availableSpecies
      .filter((item) => item.id !== current.id && item.mature_crown_diameter_max_m < current.mature_crown_diameter_max_m)
      .sort((left, right) => left.mature_crown_diameter_max_m - right.mature_crown_diameter_max_m)[0];
  }, [availableSpecies, speciesId]);
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
      const shared = {
        plant_kind: plantKind,
        composition,
        tree_share: 0.65,
        zone_ids: selectedZoneIds,
        placement_mode: 'count',
        target_count: requestedCount,
        spacing_m: spacing,
        edge_offset_m: edgeOffset,
        angle_deg: angle,
        seed,
        size_class: 'standard',
        species_revision_id: composition === 'mixed' ? undefined : speciesId,
        tree_species_revision_id: composition === 'mixed' ? speciesId : undefined,
        shrub_species_revision_id: composition === 'mixed' ? shrubSpeciesId : undefined,
        spacing_policy: spacingPolicy,
      } as const;
      if (placementScenario === 'natural') {
        onPreview({
          ...shared,
          type: 'fill',
          layout: 'natural',
        });
      } else {
        onPreview({
          ...shared,
          type: 'mask',
          mask_id: placementScenario,
          road_offset_m: 3,
          cluster_gap_m: 18,
          cluster_size: 7,
        });
      }
    }
  };

  return <div className="project-inspector pattern-tool-panel">
    <InspectorHeader title={mode === 'row' ? 'Ряд посадок' : 'Разместить посадки'} meta={mode === 'row' ? 'По выбранной линии' : 'По выбранным участкам'} />
    <div className="pattern-tool-panel__content">
      <StepProgress current={preview ? 2 : !selectedZoneIds.length ? 0 : 1} steps={PLANTING_WORKFLOW_STEPS} />
      {preview ? <section className="pattern-result-review" aria-label="Результат расчёта">
        <section className="pattern-live-summary" aria-live="polite"><span><strong>{preview.accepted_count ? `Найдено ${preview.accepted_count}` : 'Мест не найдено'}</strong><small>{preview.accepted_count === preview.requested_count ? 'Все позиции проходят проверку' : `Проверено до ${preview.requested_count}`}</small></span>{mode === 'fill' && preview.effective_spacing_m ? <b>шаг {preview.effective_spacing_m} м</b> : null}</section>
        {preview.reason_summary?.length ? <HelpDisclosure title="Почему меньше"><ul>{preview.reason_summary.slice(0, 4).map((item) => <li key={`${item.status}:${item.code}`}>{item.count} — {item.message}</li>)}</ul></HelpDisclosure> : null}
        {!preview.accepted_count && compactAlternative ? <section className="pattern-next-action"><span><strong>Попробуйте компактную породу</strong><small>Её прогнозная крона занимает меньше места</small></span><Button variant="secondary" onClick={() => { setSpeciesId(compactAlternative.id); onResetPreview?.(); }}>Выбрать {compactAlternative.common_name}</Button></section> : null}
        {preview.change_set && onGrowthHorizon ? <GrowthHorizonControl value={growthHorizon} forecasts={selectedSpecies ? [selectedSpecies] : []} onChange={onGrowthHorizon} /> : null}
      </section> : <>
      <PlantingZonePicker zones={zones} selectedIds={selectedZoneIds} disabled={loading} drawing={drawingZone} onChange={(ids) => onSelectedZoneIdsChange?.(ids)} onCreate={mode === 'fill' ? onDrawZone : undefined} onCancelCreate={onCancel} />
      {mode === 'row' ? <section className="pattern-tool-panel__axis">
        <strong>{axis ? 'Линия выбрана' : 'Выберите линию на карте'}</strong>
        {axis ? <dl><dt>Источник</dt><dd>{sourceLayerLabel(axisSource?.label)}</dd><dt>Длина</dt><dd>{axisLength?.toFixed(1)} м</dd></dl> : <span>Кликните по линии DXF. Shift — нарисовать свою ось</span>}
      </section> : null}
      {mode === 'fill' && canPreview && !drawingZone ? <PlacementScenarioPicker value={placementScenario} presets={placementMasks} disabled={loading} onChange={setPlacementScenario} /> : null}
      {drawingZone ? <InlineMessage tone="info">Поставьте точки по границе участка и замкните контур</InlineMessage> : null}
      {!drawingZone && canPreview ? <>
      <FormField label="Состав группы"><Select aria-label="Состав группы" value={composition} onChange={(event) => { const value = event.target.value as typeof composition; setComposition(value); setSpacing(value === 'shrubs' ? 2 : 6); }}><option value="trees">Деревья</option><option value="shrubs">Кустарники</option>{mode === 'fill' ? <option value="mixed">Деревья и кустарники</option> : null}</Select></FormField>
      <FormField label="Порода"><Select aria-label="Порода для участка" value={speciesId} disabled={shortlistLoading} onChange={(event) => setSpeciesId(event.target.value)}>{availableSpecies.map((item) => <option key={item.id} value={item.id}>{item.common_name}</option>)}</Select></FormField>
      {selectedSpecies ? (() => { const forecast = forecastAt(selectedSpecies.canopy_forecast, growthHorizon ?? 20); const rootLabel = selectedSpecies.root_architecture === 'shallow' ? 'поверхностные' : selectedSpecies.root_architecture === 'deep' ? 'глубокие' : selectedSpecies.root_architecture === 'mixed' ? 'смешанные' : 'не подтверждены'; const crownClass = selectedSpecies.mature_crown_diameter_max_m <= 5 ? 'компактная крона' : selectedSpecies.mature_crown_diameter_max_m <= 8 ? 'средняя крона' : 'широкая крона'; return <section className="species-preview-card"><span><strong>{selectedSpecies.common_name}</strong><small>{rootLabel} корни</small></span><span><b>{forecast ? `${(forecast.radius_min_m * 2).toFixed(1)}–${(forecast.radius_max_m * 2).toFixed(1)} м` : 'нет прогноза'}</b><small>{crownClass}</small></span></section>; })() : null}
      {composition === 'mixed' ? <FormField label="Порода кустарника"><Select aria-label="Порода кустарника" value={shrubSpeciesId} onChange={(event) => setShrubSpeciesId(event.target.value)}>{availableShrubs.map((item) => <option key={item.id} value={item.id}>{item.common_name}</option>)}</Select></FormField> : null}
      {mode === 'row' ? <Disclosure title="Дополнительные настройки">
        <FormField label="Плотность группы"><Select value={spacingPolicy} onChange={(event) => setSpacingPolicy(event.target.value as typeof spacingPolicy)}><option value="canopy">Плотно, кроны сомкнутся</option><option value="balanced">Естественно</option><option value="open">Свободно</option></Select></FormField>
        {mode === 'row' ? <FormField label="Сторона оси"><Select value={side} onChange={(event) => setSide(event.target.value as typeof side)}><option value="center">По оси</option><option value="left">Слева</option><option value="right">Справа</option><option value="both">С двух сторон</option></Select></FormField> : null}
        {mode === 'row' ? <FormField label="Задать ряд"><Select value={rowPlacementMode} onChange={(event) => setRowPlacementMode(event.target.value as typeof rowPlacementMode)}><option value="count">По количеству</option><option value="spacing">По шагу</option></Select></FormField> : null}
        {mode === 'row' && rowPlacementMode === 'count' ? <InspectorSettingRow label="Количество"><NumberStepper label="Количество посадок" value={targetCount} onChange={setTargetCount} min={2} max={5000} step={10} /></InspectorSettingRow> : null}
        {mode === 'row' && rowPlacementMode === 'spacing' ? <InspectorSettingRow label="Шаг, м"><NumberStepper label="Шаг между посадками" value={spacing} onChange={setSpacing} min={plantKind === 'tree' ? 5 : 1.6} max={30} step={plantKind === 'tree' ? 0.5 : 0.2} /></InspectorSettingRow> : null}
        {mode === 'row' && side !== 'center' ? <InspectorSettingRow label="От оси, м"><NumberStepper label="Поперечный отступ" value={lateralOffset} onChange={setLateralOffset} min={0.5} max={30} step={0.5} /></InspectorSettingRow> : null}
        {mode === 'row' ? <><InspectorSettingRow label="От начала, м"><NumberStepper label="Отступ от начала" value={startOffset} onChange={setStartOffset} min={0} max={100} step={0.5} /></InspectorSettingRow><InspectorSettingRow label="От конца, м"><NumberStepper label="Отступ от конца" value={endOffset} onChange={setEndOffset} min={0} max={100} step={0.5} /></InspectorSettingRow></> : null}
      </Disclosure> : <><FormField label="Плотность группы"><Select value={spacingPolicy} onChange={(event) => setSpacingPolicy(event.target.value as typeof spacingPolicy)}><option value="canopy">Плотно</option><option value="balanced">Естественно</option><option value="open">Свободно</option></Select></FormField><InspectorSettingRow label="Количество"><NumberStepper label="Количество посадок" value={targetCount} onChange={setTargetCount} min={1} max={5000} step={10} /></InspectorSettingRow></>}
      {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
      </> : null}
      </>}
    </div>
    <div className="inspector-spacer" />
    {!drawingZone ? <InspectorFooter>{preview?.change_set ? <><Button variant="secondary" disabled={loading} onClick={onResetPreview}>Изменить</Button><Button variant="primary" loading={loading} onClick={onApply}>{`Добавить ${preview.accepted_count}`}</Button></> : preview ? <Button variant="primary" disabled={loading} onClick={onResetPreview ?? onCancel}>Изменить условия</Button> : <><Button variant="secondary" disabled={loading} onClick={onCancel}>Отмена</Button><Button variant="primary" loading={loading} disabled={!canPreview || !speciesId || (composition === 'mixed' && !shrubSpeciesId)} onClick={submit}>{!selectedZoneIds.length ? 'Выберите участок' : mode === 'row' && !axis ? 'Выберите линию' : 'Проверить места'}</Button></>}</InspectorFooter> : null}
  </div>;
}
