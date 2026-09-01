import { cleanup, render, screen } from '@testing-library/react';
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
  it('explains a blocked group move and keeps apply disabled', () => {
    render(<ChangeSetReviewPanel preview={preview('blocked')} onApply={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByText('Перемещение недоступно')).toBeVisible();
    expect(screen.getByText(/Недостаточный отступ от дороги.*Сместите группу вправо/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Применить' })).toBeDisabled();
  });

  it('separates an unresolved source from a hard prohibition', () => {
    render(<ChangeSetReviewPanel preview={preview('unknown')} onApply={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByText('Нужна проверка')).toBeVisible();
    expect(screen.queryByText('Перемещение недоступно')).not.toBeInTheDocument();
  });
});
