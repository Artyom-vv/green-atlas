import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import type { PlanComparisonMode } from '@/entities/editor/model/planComparison';
import { MapPlanComparison } from './MapPlanComparison';

afterEach(cleanup);

describe('map comparison controls', () => {
  it('enters the applied result and provides accessible before/after and return actions', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <MapPlanComparison
        mode="editing"
        canEnter
        hasDraft={false}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Сравнить до/после' }));
    expect(onChange).toHaveBeenLastCalledWith('after');
    rerender(
      <MapPlanComparison mode="after" canEnter hasDraft onChange={onChange} />,
    );
    expect(screen.getByRole('button', { name: 'После' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Применённые посадки. Черновик не включён',
    );
    fireEvent.click(screen.getByRole('button', { name: 'До' }));
    expect(onChange).toHaveBeenLastCalledWith('before');
    rerender(
      <MapPlanComparison mode="before" canEnter hasDraft onChange={onChange} />,
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Исходная территория. Черновик не включён',
    );
    fireEvent.click(screen.getByRole('button', { name: 'К редактированию' }));
    expect(onChange).toHaveBeenLastCalledWith('editing');
  });

  it('does not enter a comparison while the current operation is pending', () => {
    const onChange = vi.fn();
    render(
      <MapPlanComparison
        mode="editing"
        canEnter={false}
        hasDraft={false}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Сравнить до/после' }));
    expect(onChange).not.toHaveBeenCalled();
  });
});

it('toggles directly to the territory without plan plantings and back while retaining focus', () => {
  const onChange = vi.fn();
  function Stateful() {
    const [mode, setMode] = useState<PlanComparisonMode>('editing');
    return (
      <MapPlanComparison
        embedded
        mode={mode}
        canEnter
        hasDraft={false}
        onChange={(next) => {
          onChange(next);
          setMode(next);
        }}
      />
    );
  }
  render(<Stateful />);
  const toggle = screen.getByRole('button', {
    name: 'Показать без посадок плана',
  });
  toggle.focus();
  fireEvent.click(toggle);
  expect(toggle).toHaveFocus();
  expect(toggle).toHaveAttribute('aria-pressed', 'true');
  expect(onChange).toHaveBeenLastCalledWith('before');
  expect(screen.getAllByRole('button')).toEqual([toggle]);
  expect(screen.queryByRole('button', { name: 'До' })).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'После' }),
  ).not.toBeInTheDocument();
  fireEvent.click(toggle);
  expect(toggle).toHaveFocus();
  expect(toggle).toHaveAttribute('aria-pressed', 'false');
  expect(onChange).toHaveBeenLastCalledWith('editing');
  expect(
    screen.getByRole('button', { name: 'Показать без посадок плана' }),
  ).toBe(toggle);
});
