import { useEffect, useMemo, useRef, useState } from 'react';
import type { FillPatternRequest, PatternPreview, PlacementMaskPreset, PlacementMaskRequest, PlantingZoneAssignment, RowPatternRequest, SpeciesRevision, SpeciesShortlistItem } from '@green/api-client';
import { Button, InlineMessage, Select } from '@green/ui';
import { WorkflowSteps } from './WorkflowSteps';
import { EditorActions, EditorDisclosure, EditorField, EditorNumber, EditorPanel } from './EditorPanel';
import { GrowthHorizonControl, type GrowthHorizon } from './GrowthHorizonControl';
import { SpeciesCatalog, SpeciesPicker } from './SpeciesPicker';
import { sourceLayerLabel } from './sourceLabels';
import { PlantingZonePicker } from './PlantingZonePicker';
import { PlacementAllocation } from './PlacementAllocation';
import { allocationHint } from './placementAllocationHint';
import { PlacementScenarioPicker, type PlacementScenarioId } from './PlacementScenarioPicker';

import { rowSketch, type RowSketchSettings } from './rowSketch';
import { Crosshair, PenLine, MousePointer2, ArrowLeftRight } from 'lucide-react';

type Axis = RowPatternRequest['axis'];
type PatternDraft = Omit<RowPatternRequest, 'base_plan_version'> | Omit<FillPatternRequest, 'base_plan_version'> | Omit<PlacementMaskRequest, 'base_plan_version'>;

export function PatternToolPanel({ mode, guided = false, zones, species, shortlist, shortlistLoading, placementMasks, axis, axisSource, axisMode = 'pick', axisDrawingPoints = 0, onAxisModeChange, onReverseAxis, onFitAxis, onFinishAxis, onRowSettingsChange, selectedZoneIds = [], drawingZone = false, loading, calculating = false, onCancelCalculation, error, preview, growthHorizon, onGrowthHorizon, onSelectedZoneIdsChange, onDrawZone, onPreview, onApply, onResetPreview, onCancel }: {
  mode: 'row' | 'fill';
  guided?: boolean;
  zones: PlantingZoneAssignment[];
  species: SpeciesRevision[];
  shortlist?: SpeciesShortlistItem[];
  shortlistLoading?: boolean;
  placementMasks?: PlacementMaskPreset[];
  axis?: Axis;
  axisSource?: { type: 'dxf' | 'manual'; label: string };
  axisMode?: 'pick' | 'draw' | 'ready';
  axisDrawingPoints?: number;
  onAxisModeChange?: (mode: 'pick' | 'draw') => void;
  onReverseAxis?: () => void;
  onFitAxis?: () => void;
  onFinishAxis?: () => void;
  onRowSettingsChange?: (settings: RowSketchSettings) => void;
  selectedZoneIds?: string[];
  drawingZone?: boolean;
  loading?: boolean;
  calculating?: boolean;
  onCancelCalculation?: () => void;
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
  const [step, setStep] = useState(0);
  const [catalog, setCatalog] = useState<'tree' | 'shrub'>();
  const bodyRef = useRef<HTMLDivElement>(null);
  const hasPreview = Boolean(preview);
  useEffect(() => { if (guided) bodyRef.current?.focus({ preventScroll: true }); }, [guided, step, catalog, hasPreview]);
  const [shrubSpeciesId, setShrubSpeciesId] = useState<string>();
  const [targetCount, setTargetCount] = useState(40);
  const [zoneDistribution, setZoneDistribution] = useState<'equal' | 'available'>('equal');
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

  const rowSettings = useMemo<RowSketchSettings>(() => ({ placementMode: rowPlacementMode, count: targetCount, spacing, side, lateralOffset, startOffset, endOffset, kind: plantKind }), [rowPlacementMode, targetCount, spacing, side, lateralOffset, startOffset, endOffset, plantKind]);
  useEffect(() => { if (mode === 'row') onRowSettingsChange?.(rowSettings); }, [mode, onRowSettingsChange, rowSettings]);
  const sketch = useMemo(() => rowSketch(axis as Parameters<typeof rowSketch>[0], rowSettings), [axis, rowSettings]);
  const canPreview = selectedZoneIds.length > 0 && (mode === 'row' ? Boolean(axis) && axisMode !== 'draw' && !sketch.invalidOffsets : true);
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
        zone_distribution: zoneDistribution,
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

  const currentStep = preview ? 3 : step;
  const validSpecies = Boolean(speciesId && (composition !== 'mixed' || shrubSpeciesId));
  const editPreview = () => { setStep(2); onResetPreview?.(); };
  return <EditorPanel title={mode === 'row' ? 'Ряд посадок' : 'Разместить посадки'}>
    {guided ? <WorkflowSteps labels={['Участки', 'Состав', 'Размещение', 'Проверка']} current={currentStep} label="Шаги размещения" /> : null}
    <div className="pattern-body" ref={bodyRef} tabIndex={-1}>
    {catalog ? <section className="placement-catalog" aria-label="Выбор породы"><h3>{catalog === 'shrub' ? 'Порода кустарника' : 'Порода посадок'}</h3><SpeciesCatalog species={catalog === 'shrub' ? availableShrubs : availableSpecies} value={catalog === 'shrub' ? shrubSpeciesId : speciesId} disabled={loading || shortlistLoading} onChange={id => { if (catalog === 'shrub') setShrubSpeciesId(id); else setSpeciesId(id); setCatalog(undefined); }} /></section> : null}
    <div hidden={Boolean(catalog)}>
    {preview ? <section aria-label="Результат расчёта">
      <h3>{preview.accepted_count ? `Найдено ${preview.accepted_count} из ${preview.requested_count}` : 'Мест не найдено'}</h3>
      {!preview.accepted_count ? <p className="editor-panel__hint">Запрошено: {preview.requested_count}</p> : null}
      {preview.effective_spacing_m ? <p className="editor-panel__hint">Минимальное расстояние: {preview.effective_spacing_m} м</p> : null}
      {preview.accepted_count > 0 && selectedSpecies && onGrowthHorizon ? <section className="placement-result-growth" aria-label="Прогноз на карте"><GrowthHorizonControl value={growthHorizon} forecasts={preview.change_set?.additions ?? []} onChange={onGrowthHorizon} /></section> : null}
      {selectedZoneIds.length > 1 ? <PlacementAllocation zones={zones} selectedIds={selectedZoneIds} preview={preview} /> : null}
      {preview.accepted_count < preview.requested_count && preview.reason_summary?.length ? <EditorDisclosure title="Почему меньше"><ul className="editor-problem-list">{preview.reason_summary.map((item, index) => <li key={`${item.status}:${item.code}:${index}`}>{item.count} — {item.message}</li>)}</ul></EditorDisclosure> : null}
      {preview.unverified_data?.length ? <EditorDisclosure title="Ограничения проверки"><ul>{preview.unverified_data.map((item, index) => <li key={index}>{item}</li>)}</ul></EditorDisclosure> : null}
      {!preview.accepted_count && compactAlternative ? <EditorActions><Button variant="secondary" onClick={() => { setSpeciesId(compactAlternative.id); onResetPreview?.(); }}>Выбрать {compactAlternative.common_name}</Button></EditorActions> : null}
    </section> : null}
    <div className={mode === 'fill' ? 'placement-setup' : undefined} hidden={Boolean(preview)}>
      <div hidden={guided && step !== 0}><PlantingZonePicker expanded={guided} zones={zones} selectedIds={selectedZoneIds} disabled={loading || Boolean(preview)} drawing={drawingZone} onChange={ids => onSelectedZoneIdsChange?.(ids)} onCreate={mode === 'fill' ? onDrawZone : undefined} onCancelCreate={onCancel} /></div>
      {mode === 'row' ? <section className="row-axis-control" aria-label="Линия посадок">
        <EditorActions grid>
          <Button variant="secondary" icon={MousePointer2} aria-pressed={axisMode === 'pick'} disabled={loading} onClick={() => onAxisModeChange?.('pick')}>Выбрать в DXF</Button>
          <Button variant="secondary" icon={PenLine} aria-pressed={axisMode === 'draw'} disabled={loading} onClick={() => onAxisModeChange?.('draw')}>Нарисовать линию</Button>
        </EditorActions>
        {axisMode === 'draw' ? <><p className="editor-panel__hint">Отметьте точки на карте. Завершите двойным щелчком или кнопкой.</p><Button variant="secondary" disabled={axisDrawingPoints < 2} onClick={onFinishAxis}>Завершить линию</Button></>
          : axisMode === 'pick' ? <p className="editor-panel__hint">Наведите на линию чертежа и выберите её щелчком.</p> : null}
        {axis ? <><dl className="editor-panel__metrics"><dt>Источник</dt><dd>{axisSource?.type === 'manual' ? 'Своя линия' : sourceLayerLabel(axisSource?.label)}</dd><dt>Длина</dt><dd>{axisLength.toFixed(1)} м</dd></dl>
          <EditorActions grid><Button variant="secondary" icon={Crosshair} onClick={onFitAxis}>Показать линию</Button><Button variant="secondary" icon={ArrowLeftRight} disabled={loading} onClick={onReverseAxis}>Развернуть направление</Button></EditorActions>
          {axisMode !== 'draw' && !preview ? <div className="row-sketch-summary" role="status">{sketch.invalidOffsets ? 'Отступы длиннее линии. Уменьшите их.' : <><strong>Эскиз: {sketch.total} позиций</strong><span>Синие точки ещё не проверены. Красные — за выбранными участками.</span></>}</div> : null}
        </> : null}
      </section> : null}
      {drawingZone ? <p className="editor-panel__hint">Поставьте точки по границе участка и замкните контур</p> : null}
      {!drawingZone && canPreview ? <>
        <fieldset className="editor-fieldset" aria-label="Посадки" disabled={loading || Boolean(preview)} hidden={guided && step === 0}>
          <div className="placement-fields" hidden={guided && step !== 1}>
          <EditorField label="Состав"><Select aria-label="Состав группы" value={composition} onChange={event => { const value = event.target.value as typeof composition; setComposition(value); setSpacing(value === 'shrubs' ? 2 : 6); }}><option value="trees">Деревья</option><option value="shrubs">Кустарники</option>{mode === 'fill' ? <option value="mixed">Смешанный</option> : null}</Select></EditorField>
          <EditorField label="Порода"><SpeciesPicker label="Порода для участка" species={availableSpecies} value={speciesId} disabled={shortlistLoading || loading || Boolean(preview)} onChange={setSpeciesId} onBrowse={guided ? () => setCatalog('tree') : undefined} /></EditorField>
          {composition === 'mixed' ? <EditorField label="Кустарник"><SpeciesPicker label="Порода кустарника" species={availableShrubs} value={shrubSpeciesId} disabled={loading || Boolean(preview)} onChange={setShrubSpeciesId} onBrowse={guided ? () => setCatalog('shrub') : undefined} /></EditorField> : null}
          </div>
          <div className={`placement-fields${mode === 'fill' ? ' placement-fields--parameters' : ''}`} hidden={guided && step !== 2}>
          {mode === 'row' ? <EditorField label="Задать ряд"><Select aria-label="Задать ряд" value={rowPlacementMode} onChange={event => setRowPlacementMode(event.target.value as typeof rowPlacementMode)}><option value="count">По количеству</option><option value="spacing">По шагу</option></Select></EditorField> : null}
          {mode === 'fill' || rowPlacementMode === 'count' ? <EditorField label={mode === 'row' && side === 'both' ? 'Всего посадок' : 'Количество'}><EditorNumber label="Количество посадок" value={targetCount} onChange={setTargetCount} min={mode === 'row' ? 2 : 1} max={5000} step={1} /></EditorField> : <EditorField label="Шаг, м"><EditorNumber label="Шаг между посадками" value={spacing} onChange={setSpacing} min={plantKind === 'tree' ? 5 : 1.6} max={30} step={plantKind === 'tree' ? .5 : .2} /></EditorField>}
          <EditorField label="Плотность"><Select aria-label="Плотность группы" value={spacingPolicy} onChange={event => setSpacingPolicy(event.target.value as typeof spacingPolicy)}><option value="canopy">Плотно</option><option value="balanced">Естественно</option><option value="open">Свободно</option></Select></EditorField>
          {mode === 'fill' && selectedZoneIds.length > 1 ? <div className="placement-distribution"><EditorField label="Между участками"><Select aria-label="Распределение посадок" value={zoneDistribution} onChange={event => setZoneDistribution(event.target.value as typeof zoneDistribution)}><option value="equal">Поровну по участкам</option><option value="available">По доступным местам</option></Select></EditorField><p className="editor-panel__hint">{zoneDistribution === 'equal' ? allocationHint(targetCount, new Set(selectedZoneIds).size) : 'Количество общее для всех выбранных участков'}</p></div> : null}
          {mode === 'row' ? <><EditorField label="От начала к концу"><Select aria-label="Сторона оси" value={side} onChange={event => setSide(event.target.value as typeof side)}><option value="center">По оси</option><option value="left">Слева</option><option value="right">Справа</option><option value="both">С двух сторон</option></Select></EditorField>
            <EditorDisclosure title="Отступы">
              {side !== 'center' ? <EditorField label="От оси, м"><EditorNumber label="Поперечный отступ" value={lateralOffset} onChange={setLateralOffset} min={.5} max={30} step={.5} /></EditorField> : null}
              <EditorField label="От начала, м"><EditorNumber label="Отступ от начала" value={startOffset} onChange={setStartOffset} min={0} max={100} step={.5} /></EditorField>
              <EditorField label="От конца, м"><EditorNumber label="Отступ от конца" value={endOffset} onChange={setEndOffset} min={0} max={100} step={.5} /></EditorField>
            </EditorDisclosure></> : null}
          </div>
        </fieldset>
        {mode === 'fill' && (!guided || step === 2) ? <PlacementScenarioPicker value={placementScenario} presets={placementMasks} disabled={loading || Boolean(preview)} onChange={setPlacementScenario} /> : null}
      </> : null}
    </div>
    {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    </div></div>
    {!drawingZone ? <EditorActions>{calculating && onCancelCalculation ? <><Button variant="secondary" onClick={onCancelCalculation}>Отменить расчёт</Button><Button variant="primary" loading disabled>Проверяем</Button></> : catalog ? <Button variant="secondary" onClick={() => setCatalog(undefined)}>Назад к составу</Button> : preview?.change_set ? <><Button variant="secondary" disabled={loading} onClick={editPreview}>Изменить</Button><Button variant="primary" loading={loading} disabled={!preview.change_set.can_apply || !preview.accepted_count} onClick={onApply}>{`Добавить ${preview.accepted_count}`}</Button></> : preview ? <Button variant="primary" disabled={loading} onClick={onResetPreview ? editPreview : onCancel}>Изменить условия</Button> : guided && step < 2 ? <><Button variant="secondary" disabled={loading} onClick={step ? () => setStep(step - 1) : onCancel}>{step ? 'Назад' : 'Отмена'}</Button><Button variant="primary" disabled={loading || (step === 0 ? !selectedZoneIds.length : !validSpecies || shortlistLoading)} onClick={() => setStep(step + 1)}>{step === 0 ? 'Выбрать состав' : 'Настроить размещение'}</Button></> : <><Button variant="secondary" disabled={loading} onClick={guided ? () => setStep(1) : onCancel}>{guided ? 'Назад' : 'Отмена'}</Button><Button variant="primary" loading={loading} disabled={!canPreview || !validSpecies || shortlistLoading} onClick={submit}>{!selectedZoneIds.length ? 'Выберите участок' : mode === 'row' && !axis ? 'Выберите линию' : 'Проверить места'}</Button></>}</EditorActions> : null}
  </EditorPanel>;
}
