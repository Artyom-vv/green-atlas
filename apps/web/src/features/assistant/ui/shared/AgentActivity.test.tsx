import { AgentActivity } from '@/features/assistant/ui/shared/AgentActivity';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

afterEach(cleanup);

describe('AgentActivity', () => {
  it('keeps the activity trace collapsed and reveals readable steps on demand', () => {
    render(
      <AgentActivity
        trace={{
          events: [
            {
              ok: true,
              tool: 'select_zone_by_spatial_intent',
              summary: { label: 'Участок 6', anchor: 'edge' },
            },
            {
              ok: true,
              tool: 'road_targets',
              summary: { total: 2, internal_id: 'road-secret' },
            },
            {
              ok: true,
              tool: 'prepare_placement',
              summary: { can_apply: true, found: 12 },
            },
          ],
        }}
      />,
    );

    const toggle = screen.getByRole('button', { name: /Ход работы/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Проверил улицы')).not.toBeVisible();
    fireEvent.click(screen.getByText('Ход работы'));
    expect(screen.getByText('Выбрал участок')).toBeVisible();
    expect(screen.getByText('Участок 6')).toBeVisible();
    expect(screen.getByText('Проверил улицы')).toBeVisible();
    expect(screen.queryByText('road-secret')).toBeNull();
  });

  it('shows one compact live status while the agent is running', () => {
    render(<AgentActivity pending="thinking" />);
    expect(screen.getByRole('status')).toHaveTextContent('Проверяю задачу');
    expect(screen.queryByText('Ход работы')).toBeNull();
  });
});
