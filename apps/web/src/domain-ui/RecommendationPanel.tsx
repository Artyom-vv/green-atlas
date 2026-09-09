import { useEffect, useRef, useState } from 'react';
import type { BuildingScreenRequest, BuildingScreenTargets, PlanningBrief, PlantingZoneAssignment, RecommendationRequest } from '@green/api-client';
import { Button, InlineMessage, Select } from '@green/ui';
import { WorkflowSteps } from './WorkflowSteps';
import { EditorActions, EditorField, EditorNumber, EditorPanel } from './EditorPanel';
import { PlantingZonePicker } from './PlantingZonePicker';

type Draft = Omit<RecommendationRequest, 'base_plan_version'>;

export function RecommendationPanel({ zones, guided = false, active = true, selectedZoneIds, onSelectedZoneIdsChange, loading, calculating = false, onCancelCalculation, error, onPreview, onCancel, onInterpret, onChooseComposition, onScreenMode, onScreenPreview, screenTargets, screenLoading, screenError }: {
  zones: PlantingZoneAssignment[];
  guided?: boolean;
  active?: boolean;
  selectedZoneIds?: string[];
  onSelectedZoneIdsChange?: (ids: string[]) => void;
  loading?: boolean;
  calculating?: boolean;
  onCancelCalculation?: () => void;
  error?: string;
  onPreview: (draft: Draft) => void;
  onCancel: () => void;
  onInterpret?: (task: string, signal: AbortSignal) => Promise<PlanningBrief>;
  onChooseComposition?: () => void;
  onScreenMode?: (active: boolean) => void;
  onScreenPreview?: (draft: Omit<BuildingScreenRequest, 'base_plan_version'>) => void;
  screenTargets?: BuildingScreenTargets;
  screenLoading?: boolean;
  screenError?: string;
}) {
  const availableIds = (items: PlantingZoneAssignment[]) => items.flatMap((zone) => zone.id ? [zone.id] : []);
  const [zoneIds, setZoneIds] = useState<string[]>(() => availableIds(zones));
  const [profile, setProfile] = useState<RecommendationRequest['profile']>('balanced');
  const [maxSites, setMaxSites] = useState(40);
  const [step, setStep] = useState(0);
  const [task, setTask] = useState('');
  const [enteringTask, setEnteringTask] = useState(true);
  const [interpreting, setInterpreting] = useState(false);
  const [taskFeedback, setTaskFeedback] = useState<string[]>([]);
  const [fromAssistant, setFromAssistant] = useState(false);
  const [requiresComposition, setRequiresComposition] = useState(false);
  const [buildingScreen, setBuildingScreen] = useState(false);
  const [screenSide, setScreenSide] = useState<'perimeter' | 'roads'>('perimeter');
  const [screenLimit, setScreenLimit] = useState<number | null>(null);
  useEffect(() => { onScreenMode?.(active && buildingScreen); }, [active, buildingScreen, onScreenMode]);
  const taskAbort = useRef<AbortController | null>(null);
  const taskInput = useRef<HTMLTextAreaElement | null>(null);
  const body = useRef<HTMLDivElement | null>(null);
  useEffect(() => () => taskAbort.current?.abort(), []);
  const textEntry = Boolean(onInterpret) && enteringTask;
  useEffect(() => {
    if (body.current) body.current.scrollTop = 0;
    if (textEntry && (!guided || step === 1)) taskInput.current?.focus();
  }, [step, textEntry, guided]);
  const manualEntry = () => {
    taskAbort.current?.abort(); setInterpreting(false); setEnteringTask(false);
    setTaskFeedback([]); setFromAssistant(false); setRequiresComposition(false);
    setBuildingScreen(false); onScreenMode?.(false);
  };
  const interpret = async () => {
    if (!onInterpret) return;
    const controller = new AbortController();
    taskAbort.current?.abort(); taskAbort.current = controller;
    setInterpreting(true); setTaskFeedback([]); setRequiresComposition(false);
    try {
      const result = await onInterpret(task.trim(), controller.signal);
      if (controller.signal.aborted) return;
      if (result.arrangement === 'building_screen' && !result.unsupported.length && onScreenPreview) {
        setBuildingScreen(true); setScreenLimit(result.max_sites); setFromAssistant(true);
        setEnteringTask(false); onScreenMode?.(true);
        return;
      }
      if (result.unsupported.length || result.questions.length || result.profile === null || result.max_sites === null) {
        setRequiresComposition(Boolean(result.unsupported.length));
        setTaskFeedback(result.unsupported.length
          ? ['Эти требования пока нужно настроить вручную:', ...result.unsupported]
          : result.questions.length ? result.questions : ['Укажите приоритет и максимальное количество посадок.']);
        return;
      }
      setProfile(result.profile); setMaxSites(result.max_sites);
      setFromAssistant(true); setEnteringTask(false);
    } catch (cause) {
      if (!controller.signal.aborted) setTaskFeedback([cause instanceof Error ? cause.message : 'Не удалось разобрать задание.']);
    } finally {
      if (!controller.signal.aborted) setInterpreting(false);
    }
  };
  const workZoneIds = selectedZoneIds ?? zoneIds;

  useEffect(() => {
    const available = new Set(availableIds(zones));
    setZoneIds((current) => {
      const retained = current.filter((id) => available.has(id));
      return retained.length ? retained : availableIds(zones);
    });
  }, [zones]);

  return <EditorPanel title="Подобрать по цели">
    {guided ? <WorkflowSteps labels={['Участки', 'Задача', 'Проверка']} current={step} label="Шаги подбора" /> : null}
    <div className="pattern-body" ref={body}>
    <div hidden={guided && step !== 0}>
    <PlantingZonePicker expanded={guided} zones={zones} selectedIds={workZoneIds} onChange={onSelectedZoneIdsChange ?? setZoneIds} disabled={loading} />
    </div><div hidden={guided && step !== 1}>
    {textEntry ? <div className="planning-task">
      <h3><label htmlFor="planning-task">Что нужно получить?</label></h3>
      <textarea ref={taskInput} id="planning-task" className="ui-input" aria-label="Задача озеленения" placeholder="Например: прикрыть здания группами деревьев" rows={4} maxLength={2000} value={task} disabled={interpreting} onChange={event => { setTask(event.target.value); setTaskFeedback([]); setRequiresComposition(false); }} />
      {taskFeedback.length ? <div role="status" className="planning-task__feedback">{taskFeedback.map((item, index) => <p key={index}>{item}</p>)}</div> : null}
      <Button variant="secondary" onClick={requiresComposition && onChooseComposition ? onChooseComposition : manualEntry}>{requiresComposition && onChooseComposition ? 'Выбрать состав и количество' : 'Настроить вручную'}</Button>
    </div> : buildingScreen ? <section className="building-screen-task">
      <fieldset disabled={loading || screenLoading}>
        <legend>С какой стороны прикрыть здания?</legend>
        <div className="building-screen-task__choices">
          <label><input type="radio" name="screen-side" value="perimeter" checked={screenSide === 'perimeter'} onChange={() => setScreenSide('perimeter')} /><span>По периметру</span></label>
          <label aria-disabled={!screenTargets?.has_roads}><input type="radio" name="screen-side" value="roads" disabled={!screenTargets?.has_roads} checked={screenSide === 'roads'} onChange={() => setScreenSide('roads')} /><span>Со стороны проездов</span></label>
        </div>
      </fieldset>
      <p className="editor-panel__hint" role="status">{screenLoading ? 'Находим здания на карте…' : screenError ? screenError : !screenTargets?.geometry ? 'Рядом не найдены здания. Выберите другие участки.' : 'Контуры зданий выделены на карте. Посадки появятся после расчёта.'}</p>
      {screenTargets?.geometry && !screenTargets.has_roads ? <p className="editor-panel__hint">В чертеже не найдены проезды.</p> : null}
    </section> : <>
    {fromAssistant ? <h3>Проверьте параметры</h3> : null}
    <div className="recommendation-fields"><EditorField label="Приоритет"><Select aria-label="Приоритет" value={profile} disabled={loading} onChange={event => setProfile(event.target.value as RecommendationRequest['profile'])}>
      <option value="balanced">Баланс</option><option value="shade">Тень</option><option value="continuity">Связность</option><option value="low_future_conflict">Меньше конфликтов</option>
    </Select></EditorField>
    <EditorField label="Максимум посадок"><EditorNumber label="Максимум посадок" value={maxSites} onChange={setMaxSites} min={1} max={500} disabled={loading} /></EditorField></div>
    </>}
    </div>
    {error ? <InlineMessage tone="error">{error}</InlineMessage> : null}
    </div>
    <EditorActions>{calculating && onCancelCalculation ? <><Button variant="secondary" onClick={onCancelCalculation}>Отменить расчёт</Button><Button variant="primary" loading disabled>Проверяем</Button></> : <><Button variant="secondary" disabled={loading} onClick={() => {
      taskAbort.current?.abort(); setInterpreting(false);
      if (fromAssistant && !enteringTask) { setEnteringTask(true); setFromAssistant(false); setBuildingScreen(false); onScreenMode?.(false); }
      else if (guided && step) setStep(0); else onCancel();
    }}>{guided && step ? interpreting ? 'Отменить разбор' : 'Назад' : 'Отмена'}</Button><Button variant="primary" loading={loading || interpreting} disabled={!workZoneIds.length || interpreting || Boolean(buildingScreen && (!screenTargets?.geometry || screenLoading || screenError || screenSide === 'roads' && !screenTargets.has_roads)) || Boolean((!guided || step) && textEntry && task.trim().length < 5)} onClick={() => {
      if (guided && !step) setStep(1);
      else if (textEntry) void interpret();
      else if (buildingScreen) onScreenPreview?.({ zone_ids: workZoneIds, screen_side: screenSide, max_sites: screenLimit });
      else onPreview({ zone_ids: workZoneIds, profile, max_sites: maxSites });
    }}>{guided && !step ? 'Выбрать задачу' : textEntry ? interpreting ? 'Разбираем задачу' : 'Разобрать задачу' : buildingScreen ? 'Показать на карте' : guided ? 'Проверить места' : 'Показать'}</Button></>}</EditorActions>
  </EditorPanel>;
}
