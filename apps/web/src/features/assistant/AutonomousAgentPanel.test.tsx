import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type AgentRun } from '@green/api-client';
import { AutonomousAgentPanel } from './AutonomousAgentPanel';

function run(status: AgentRun['state']['status']): AgentRun {
  return {
    state: {
      run_id: 'run-1',
      project_id: 'project-1',
      status,
      intent: { raw_text: 'Посади 10 деревьев вдоль зданий' },
      resolved_scope: { zone_ids: ['zone-1'] },
      candidate_zone_ids: ['zone-1'],
      snapshot_version: 3,
      plan_version: 2,
      step: 4,
      tool_calls: ['call-1'],
      tool_fingerprints: [],
      evidence_refs: [],
      last_result: null,
      pending_question: status === 'waiting_question' ? { slot: 'scope', question: 'Какой участок использовать?' } : null,
      pending_approval: status === 'waiting_approval' ? { preview_ref: 'call-1' } : null,
      outcome_ref: status === 'finished' ? 'change-set:1' : null,
      failure: null,
      max_steps: 64,
    },
    revision: 7,
    created_at: '',
    updated_at: '',
    events: [
      { sequence: 2, kind: 'run_started', payload: {}, created_at: '' },
      { sequence: 5, kind: 'tool_result', payload: { name: 'prepare_placement', status: 'succeeded', call_id: 'call-1', data: { proposal: { found: 10, requested: 10 } } }, created_at: '' },
      { sequence: 6, kind: 'approval_requested', payload: {}, created_at: '' },
    ],
  };
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('AutonomousAgentPanel', () => {
  it('runs an independent task and exposes one approval action', async () => {
    const waiting = run('waiting_approval');
    const finished = run('finished');
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(waiting);
    vi.spyOn(api, 'runAgentRun').mockResolvedValue(waiting);
    vi.spyOn(api, 'approveAgentRun').mockResolvedValue(finished);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><AutonomousAgentPanel projectId="project-1" onBack={vi.fn()} onClose={vi.fn()} /></QueryClientProvider>);

    fireEvent.change(screen.getByLabelText('Задача для автономного агента'), { target: { value: 'Посади 10 деревьев вдоль зданий' } });
    fireEvent.click(screen.getByRole('button', { name: 'Поставить задачу' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Применить предложение' })).toBeVisible());
    expect(api.createAgentRun).toHaveBeenCalledWith('project-1', 'Посади 10 деревьев вдоль зданий');
    expect(api.runAgentRun).toHaveBeenCalledWith('project-1', 'run-1');
    expect(screen.getByText('Расчёт размещения')).not.toBeVisible();
    fireEvent.click(screen.getByText('Ход работы'));
    expect(screen.getByText('Расчёт размещения')).toBeVisible();
    expect(screen.queryByText('chain-of-thought')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Применить предложение' }));
    await waitFor(() => expect(screen.getByText('Изменение применено к плану.')).toBeVisible());
    expect(api.approveAgentRun).toHaveBeenCalledWith('project-1', 'run-1', 'call-1');
  });

  it('answers a real checkpoint question and resumes the same run', async () => {
    const question = run('waiting_question');
    const queued = run('queued');
    const waiting = run('waiting_approval');
    vi.spyOn(api, 'createAgentRun').mockResolvedValue(question);
    vi.spyOn(api, 'runAgentRun').mockResolvedValueOnce(question).mockResolvedValueOnce(waiting);
    vi.spyOn(api, 'answerAgentRun').mockResolvedValue(queued);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><AutonomousAgentPanel projectId="project-1" onBack={vi.fn()} onClose={vi.fn()} /></QueryClientProvider>);

    fireEvent.change(screen.getByLabelText('Задача для автономного агента'), { target: { value: 'Посади 10 деревьев' } });
    fireEvent.click(screen.getByRole('button', { name: 'Поставить задачу' }));
    await waitFor(() => expect(screen.getByText('Какой участок использовать?')).toBeVisible());
    fireEvent.change(screen.getByLabelText('Ответ агенту'), { target: { value: 'Участок 6' } });
    fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
    await waitFor(() => expect(api.answerAgentRun).toHaveBeenCalledWith('project-1', 'run-1', 'Участок 6'));
    expect(api.runAgentRun).toHaveBeenCalledTimes(2);
  });

});
