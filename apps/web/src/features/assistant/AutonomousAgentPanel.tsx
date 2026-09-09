import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowUp, Check, ChevronDown, CircleAlert, LoaderCircle, PanelLeftClose, Sparkles } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { api, ApiClientError, type AgentRun, type AgentRunEvent } from '@green/api-client';
import { Button, IconButton } from '@green/ui';

type Props = { projectId: string; onBack: () => void; onClose: () => void };

const terminalStatuses = new Set<AgentRun['state']['status']>(['waiting_question', 'waiting_approval', 'waiting_job', 'finished', 'failed', 'cancelled']);
const toolLabels: Record<string, string> = {
  find_zone_candidates: 'Подбор участка',
  inspect_zones: 'Проверка участка',
  species_shortlist: 'Подбор растений',
  building_targets: 'Проверка зданий',
  road_targets: 'Проверка улиц',
  prepare_placement: 'Расчёт размещения',
  prepare_existing_change: 'Подготовка изменения',
  growth_scene: 'Проверка роста',
  growth_objects: 'Проверка роста',
  plan_issues: 'Проверка ограничений',
};

function errorText(error: unknown) {
  if (error instanceof ApiClientError) return error.message;
  if (error instanceof Error) return error.message;
  return 'Не удалось продолжить запуск.';
}

function payloadText(event: AgentRunEvent) {
  const payload = event.payload;
  const name = typeof payload.name === 'string' ? payload.name : '';
  const data = payload.data && typeof payload.data === 'object' ? payload.data as Record<string, unknown> : undefined;
  if (name === 'find_zone_candidates' && typeof data?.total === 'number') return `${data.total} участков проверено`;
  if (name === 'species_shortlist' && typeof data?.total === 'number') return `${data.total} вариантов в каталоге`;
  if (name === 'prepare_placement' || name === 'prepare_existing_change') {
    const proposal = data?.proposal && typeof data.proposal === 'object' ? data.proposal as Record<string, unknown> : undefined;
    const found = typeof data?.found === 'number' ? data.found : proposal?.found;
    const requested = typeof data?.requested === 'number' ? data.requested : proposal?.requested;
    const shortfall = typeof data?.shortfall === 'number' ? data.shortfall : proposal?.shortfall;
    if (typeof found === 'number' && typeof requested === 'number') {
      return shortfall ? `${found} из ${requested} мест проходят проверку; ${shortfall} не вместилось` : `${found} из ${requested} мест проходят проверку`;
    }
    return 'Предложение проверено';
  }
  if (event.kind === 'approval_requested') return 'Можно применить к плану';
  if (event.kind === 'commit_applied') return 'Изменение применено';
  return undefined;
}

function failureText(run: AgentRun | undefined) {
  const failure = run?.state.failure;
  const code = typeof failure?.code === 'string' ? failure.code : '';
  if (code === 'AGENT_NO_PROGRESS') return 'Шаги повторились без нового результата. Измените способ размещения.';
  if (code === 'INVALID_TOOL_ARGUMENTS') return 'Один из шагов получил неполные параметры. Запустите задачу ещё раз.';
  if (code === 'STALE_PROJECT') return 'Проект изменился во время расчёта. Запустите задачу заново.';
  return 'Предложение не подготовлено. Измените задачу и повторите расчёт.';
}

function eventPresentation(event: AgentRunEvent) {
  const payload = event.payload;
  const name = typeof payload.name === 'string' ? payload.name : '';
  const status = payload.status;
  const error = payload.error && typeof payload.error === 'object' ? payload.error as Record<string, unknown> : undefined;
  const code = typeof payload.code === 'string' ? payload.code : typeof error?.code === 'string' ? error.code : '';
  const failed = ['failed', 'blocked', 'stale'].includes(String(status)) || event.kind === 'run_failed';
  const title = event.kind === 'approval_requested'
    ? 'Предложение готово'
    : event.kind === 'commit_applied'
      ? 'Изменение применено'
      : event.kind === 'question'
        ? 'Нужна одна деталь'
      : event.kind === 'run_started'
        ? 'Задача принята'
        : toolLabels[name] ?? (event.kind === 'decision' ? 'Следующий шаг' : 'Проверка данных');
  const detail = code === 'REPEATED_TOOL_CALL'
    ? 'Шаг уже выполнялся — нужна другая стратегия'
    : code === 'INVALID_TOOL_ARGUMENTS'
      ? 'Параметры шага не подошли'
      : code === 'AGENT_NO_PROGRESS'
        ? 'Шаги повторились без нового результата'
        : failed ? 'Потребовался новый расчёт' : payloadText(event);
  return { title, detail, failed };
}

function traceEvents(run: AgentRun | undefined) {
  return (run?.events ?? []).filter(event => ['run_started', 'tool_result', 'question', 'approval_requested', 'commit_applied', 'run_failed'].includes(event.kind));
}

function runStatus(run: AgentRun | undefined) {
  if (!run) return 'Готов принять задачу';
  if (run.state.status === 'queued') return 'Задача в очереди';
  if (run.state.status === 'running') return 'Проверяю проект';
  if (run.state.status === 'waiting_approval') return 'Предложение готово';
  if (run.state.status === 'waiting_question') return 'Нужен ваш ответ';
  if (run.state.status === 'finished') return 'Готово';
  if (run.state.status === 'failed') return 'Нужен повторный расчёт';
  return 'Ожидает действия';
}

export function AutonomousAgentPanel({ projectId, onBack, onClose }: Props) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState('');
  const [run, setRun] = useState<AgentRun>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const historyRef = useRef<HTMLDivElement>(null);
  const requestRef = useRef(0);
  const events = useMemo(() => traceEvents(run), [run]);
  const active = Boolean(run && !terminalStatuses.has(run.state.status));
  const approval = run?.state.status === 'waiting_approval' ? run.state.pending_approval : undefined;

  useEffect(() => {
    if (!run || terminalStatuses.has(run.state.status)) return undefined;
    let disposed = false;
    const refresh = async () => {
      try {
        const next = await api.getAgentRun(projectId, run.state.run_id);
        if (!disposed) {
          setRun(next);
          setError(undefined);
        }
      } catch (cause) {
        if (!disposed) setError(errorText(cause));
      }
    };
    const timer = window.setInterval(() => void refresh(), 1200);
    void refresh();
    return () => { disposed = true; window.clearInterval(timer); };
  }, [projectId, run?.state.run_id, run?.state.status]);

  useEffect(() => {
    if (historyRef.current) historyRef.current.scrollTop = historyRef.current.scrollHeight;
  }, [events.length, run?.state.status]);

  const start = async () => {
    const text = draft.trim();
    if (!text || busy || active) return;
    const token = ++requestRef.current;
    setBusy(true);
    setError(undefined);
    try {
      const created = await api.createAgentRun(projectId, text);
      if (token !== requestRef.current) return;
      setRun(created);
      setDraft('');
      void api.runAgentRun(projectId, created.state.run_id).then(next => {
        if (token === requestRef.current) setRun(next);
      }).catch(cause => {
        if (token === requestRef.current) setError(errorText(cause));
      });
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!run || !approval || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      const next = await api.approveAgentRun(projectId, run.state.run_id, approval.preview_ref);
      setRun(next);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['workspace-project', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['plan-history', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['map-features', projectId] }),
      ]);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };

  const retry = () => {
    if (!run || run.state.status !== 'failed') return;
    setError(undefined);
    setBusy(true);
    void api.runAgentRun(projectId, run.state.run_id).then(setRun).catch(cause => setError(errorText(cause))).finally(() => setBusy(false));
  };

  const retryable = run?.state.failure && typeof run.state.failure.retryable === 'boolean'
    ? run.state.failure.retryable
    : false;

  const answerQuestion = async () => {
    if (!run || run.state.status !== 'waiting_question' || !draft.trim() || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      const queued = await api.answerAgentRun(projectId, run.state.run_id, draft.trim());
      setDraft('');
      setRun(queued);
      const started = await api.runAgentRun(projectId, run.state.run_id);
      setRun(started);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };

  return <div className="autonomous-agent" aria-label="Автономный агент">
    <header className="autonomous-agent__header">
      <IconButton icon={ArrowLeft} variant="ghost" label="Вернуться к помощнику" onClick={onBack} />
      <div className="autonomous-agent__heading"><Sparkles size={17} aria-hidden="true" /><h2>Автономный агент</h2></div>
      <IconButton icon={PanelLeftClose} variant="ghost" label="Закрыть агента" onClick={onClose} />
    </header>
    <div className="autonomous-agent__status" role="status" aria-live="polite"><span className={active ? 'is-active' : run?.state.status === 'failed' ? 'is-error' : 'is-ready'} aria-hidden="true" />{runStatus(run)}</div>
    <div className="autonomous-agent__history" ref={historyRef}>
      {!run ? <div className="autonomous-agent__empty"><h3>Поручите задачу целиком</h3><p>Агент сам найдёт участок, проверит условия и подготовит изменение.</p></div> : null}
      {run ? <article className="autonomous-agent__request"><span>Задача</span><p>{String(run.state.intent.raw_text ?? '')}</p></article> : null}
      {run?.state.status === 'waiting_question' && run.state.pending_question ? <section className="autonomous-agent__question" role="status">
        <span className="autonomous-agent__eyebrow">Нужна одна деталь</span>
        <p>{run.state.pending_question.question}</p>
      </section> : null}
      {events.length ? <details className="autonomous-agent__trace">
        <summary><span>Ход работы</span><strong>{events.length}</strong><ChevronDown size={15} aria-hidden="true" /></summary>
        <ol>{events.map(event => { const item = eventPresentation(event); return <li key={`${event.sequence}-${event.kind}`} className={item.failed ? 'is-error' : ''}><span aria-hidden="true">{item.failed ? <CircleAlert size={13} /> : <Check size={13} />}</span><div><strong>{item.title}</strong>{item.detail ? <small>{item.detail}</small> : null}</div></li>; })}</ol>
      </details> : null}
      {approval && run ? <section className="autonomous-agent__approval"><span className="autonomous-agent__eyebrow">Предложение проверено</span><h3>Изменить план?</h3><p>{payloadText(run.events.find(event => event.kind === 'tool_result' && event.payload.call_id === approval.preview_ref) ?? { sequence: 0, kind: 'approval_requested', payload: {}, created_at: '' }) ?? 'Изменение готово к применению.'}</p><Button variant="primary" loading={busy} onClick={() => void approve()}>Применить предложение</Button></section> : null}
      {run?.state.status === 'finished' ? <div className="autonomous-agent__complete"><Check size={16} aria-hidden="true" /><span>Изменение применено к плану.</span></div> : null}
      {run?.state.status === 'failed' ? <div className="autonomous-agent__error" role="alert"><p>{failureText(run)}</p>{retryable ? <Button variant="secondary" loading={busy} onClick={retry}>Повторить расчёт</Button> : null}</div> : null}
      {error ? <div className="autonomous-agent__error" role="alert"><p>{error}</p></div> : null}
    </div>
    <form className="autonomous-agent__composer" onSubmit={event => { event.preventDefault(); void (run?.state.status === 'waiting_question' ? answerQuestion() : start()); }}>
      <textarea aria-label={run?.state.status === 'waiting_question' ? 'Ответ агенту' : 'Задача для автономного агента'} placeholder={run?.state.status === 'waiting_question' ? 'Ваш ответ' : 'Например: посади 70 деревьев вдоль зданий, участок и породу выбери сам'} value={draft} onChange={event => setDraft(event.target.value)} maxLength={run?.state.status === 'waiting_question' ? 800 : 2000} rows={3} disabled={busy || active} />
      <Button variant="primary" icon={busy || active ? LoaderCircle : ArrowUp} loading={busy} disabled={!draft.trim() || busy || active}>{active ? 'Проверяем' : run?.state.status === 'waiting_question' ? 'Продолжить' : 'Поставить задачу'}</Button>
    </form>
  </div>;
}
