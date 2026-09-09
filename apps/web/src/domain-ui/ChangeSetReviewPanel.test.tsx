import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ChangeSetPreview } from '@green/api-client';
import { ChangeSetReviewPanel } from './ChangeSetReviewPanel';

afterEach(cleanup);

const preview = (status: 'blocked' | 'soft_conflict' | 'unknown'): ChangeSetPreview => ({
  id: 'preview-1',
  digest: 'digest',
  base_plan_version: 1,
  source: 'group',
  label: 'Перемещение группы',
  can_apply: false,
  additions: [],
  updates: [],
  deletion_ids: [],
  candidate_results: [{
    operation_index: 0,
    type: 'update',
    status,
    code: status === 'blocked' ? 'PP743_CLEARANCE' : 'ROOT_UTILITY_REVIEW',
    category: status === 'blocked' ? 'constraint' : status === 'soft_conflict' ? 'growth' : 'data',
    reason: status === 'blocked' ? 'Недостаточный отступ от дороги' : 'Тип сети не подтверждён',
    suggested_action: 'Сместите группу вправо',
  }],
  expires_at: '2026-09-01T00:00:00Z',
});

describe('ChangeSetReviewPanel', () => {
  it('lets the operator return to the map without applying or cancelling the draft', () => {
    const onInspect = vi.fn(), onApply = vi.fn(), onCancel = vi.fn();
    render(<ChangeSetReviewPanel preview={{ ...preview('unknown'), can_apply: true, candidate_results: [] }} onApply={onApply} onCancel={onCancel} onInspect={onInspect} />);
    fireEvent.click(screen.getByRole('button', { name: 'Посмотреть на карте' }));
    expect(onInspect).toHaveBeenCalledOnce();
    expect(onApply).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('counts the entire attempted change, not only candidates that passed', () => {
    const blocked = preview('blocked');
    render(<ChangeSetReviewPanel preview={{ ...blocked, candidate_results: [blocked.candidate_results![0], { ...blocked.candidate_results![0], operation_index: 1 }] }} onApply={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText('Не проходят проверку: 2')).toBeVisible();
    expect(screen.getByText('Позиций: 2')).toBeVisible();
    expect(screen.getByText('Изменить').nextElementSibling).toHaveTextContent('2');
  });

  it('cannot dismiss the confirmation while saving', () => {
    const onInspect = vi.fn(), onCancel = vi.fn();
    render(<ChangeSetReviewPanel preview={{ ...preview('unknown'), can_apply: true }} applying onApply={vi.fn()} onCancel={onCancel} onInspect={onInspect} />);
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(onInspect).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Применить' })).toBeDisabled();
  });
  it('explains a blocked group move and keeps apply disabled', () => {
    render(<ChangeSetReviewPanel preview={preview('blocked')} onApply={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByText('Изменение недоступно')).toBeVisible();
    expect(screen.getByText(/Недостаточный отступ от дороги.*Сместите группу вправо/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Применить' })).not.toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Изменение недоступно' })).toBeVisible();
  });

  it('separates an unresolved source from a hard prohibition', () => {
    render(<ChangeSetReviewPanel preview={preview('unknown')} onApply={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByText('Нужна проверка')).toBeVisible();
    expect(screen.queryByText('Изменение недоступно')).not.toBeInTheDocument();
  });
});
