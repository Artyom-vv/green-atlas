import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ValidationIssue } from '@green/api-client';
import { ValidationPanel } from './ValidationPanel';

afterEach(cleanup);

const issue = (id: string, objectId: string, description: string): ValidationIssue => ({
  id,
  severity: 'error',
  code: 'PP743_CLEARANCE',
  title: 'Недостаточный отступ от здания',
  description,
  object_id: objectId,
  rule_id: 'pp743-3.6.3-building',
  suggested_action: 'Сместите посадку от стены',
  related_object_ids: [],
});

describe('ValidationPanel', () => {
  it('groups repeated findings by basis and keeps every object locatable', () => {
    const onLocate = vi.fn();
    render(<ValidationPanel issues={[issue('one', 'tree-1', 'До стены 3 м'), issue('two', 'tree-2', 'До стены 4 м')]} onLocate={onLocate} />);

    expect(screen.getByRole('region', { name: 'Основание pp743-3.6.3-building' })).toBeVisible();
    expect(screen.getAllByText('pp743-3.6.3-building')).toHaveLength(1);
    expect(screen.getByText(/До стены 3 м.*Действие: Сместите посадку от стены/)).toBeVisible();
    const locate = screen.getAllByRole('button', { name: 'Показать' });
    expect(locate).toHaveLength(2);
    fireEvent.click(locate[1]);
    expect(onLocate).toHaveBeenCalledWith('tree-2');
  });
});
