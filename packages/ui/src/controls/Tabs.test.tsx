import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { ControlProvider } from '../foundations/ControlProvider';
import { Tabs } from './Tabs';

const options = [
  { value: 'review', label: 'Проверить', panelId: 'review-panel' },
  { value: 'unavailable', label: 'Недоступно', disabled: true },
  { value: 'included', label: 'Учтено', panelId: 'included-panel' },
] as const;

describe('Tabs', () => {
  it('keeps selection controlled and connects tabs to caller-owned panels', () => {
    const onChange = vi.fn();
    const onSubmit = vi.fn((event) => event.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <Tabs
          id="review-tabs"
          label="Просмотр слоёв"
          value="review"
          options={options}
          onChange={onChange}
        />
        <section
          id="review-panel"
          role="tabpanel"
          aria-labelledby="review-tabs-tab-review"
        />
        <section
          id="included-panel"
          role="tabpanel"
          aria-labelledby="review-tabs-tab-included"
          hidden
        />
      </form>,
    );
    expect(
      screen.getByRole('tablist', { name: 'Просмотр слоёв' }),
    ).toBeVisible();
    const review = screen.getByRole('tab', { name: 'Проверить' });
    const included = screen.getByRole('tab', { name: 'Учтено' });
    expect(review).toHaveAttribute('aria-controls', 'review-panel');
    expect(included).toHaveAttribute('aria-controls', 'included-panel');
    expect(screen.getByRole('tabpanel', { name: 'Проверить' })).toBeVisible();
    fireEvent.click(included);
    expect(onChange).toHaveBeenCalledWith('included');
    expect(review).toHaveAttribute('aria-selected', 'true');
    expect(included).toHaveAttribute('aria-selected', 'false');
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('tab', { name: 'Недоступно' }));
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('activates with arrows, Home and End without activating disabled tabs', async () => {
    function ControlledTabs() {
      const [value, setValue] = useState('review');
      return (
        <Tabs
          label="Просмотр слоёв"
          value={value}
          options={options}
          onChange={setValue}
        />
      );
    }
    render(<ControlledTabs />);
    const review = screen.getByRole('tab', { name: 'Проверить' });
    const unavailable = screen.getByRole('tab', { name: 'Недоступно' });
    const included = screen.getByRole('tab', { name: 'Учтено' });
    act(() => review.focus());
    fireEvent.keyDown(review, { key: 'ArrowRight' });
    await waitFor(() => expect(unavailable).toHaveFocus());
    expect(unavailable).toHaveAttribute('aria-disabled', 'true');
    expect(unavailable).toHaveAttribute('aria-selected', 'false');
    expect(review).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(unavailable, { key: 'ArrowRight' });
    await waitFor(() => expect(included).toHaveFocus());
    expect(included).toHaveAttribute('aria-selected', 'true');
    expect(review).toHaveAttribute('tabindex', '-1');
    fireEvent.keyDown(included, { key: 'ArrowRight' });
    await waitFor(() => expect(review).toHaveFocus());
    expect(review).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(review, { key: 'End' });
    await waitFor(() => expect(included).toHaveFocus());
    fireEvent.keyDown(included, { key: 'Home' });
    await waitFor(() => expect(review).toHaveFocus());
    fireEvent.keyDown(review, { key: 'ArrowLeft' });
    await waitFor(() => expect(included).toHaveFocus());
  });

  it('inherits shared density and allows an explicit local size', () => {
    render(
      <ControlProvider size="compact">
        <Tabs
          label="Компактно"
          value="review"
          options={options}
          onChange={vi.fn()}
        />
        <Tabs
          label="Крупнее"
          value="review"
          options={options}
          onChange={vi.fn()}
          controlSize="large"
        />
      </ControlProvider>,
    );
    const compact = screen.getByRole('tablist', { name: 'Компактно' });
    const large = screen.getByRole('tablist', { name: 'Крупнее' });
    expect(compact.parentElement).toHaveAttribute('data-size', 'compact');
    expect(large.parentElement).toHaveAttribute('data-size', 'large');
    expect(compact.querySelectorAll('[data-size="compact"]')).toHaveLength(3);
    expect(large.querySelectorAll('[data-size="large"]')).toHaveLength(3);
  });
});
