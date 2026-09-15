import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SegmentedControl } from './SegmentedControl';

describe('SegmentedControl', () => {
  it('keeps selection controlled and skips unavailable options with arrow keys', () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        label="Отображение"
        controlSize="compact"
        value="plan"
        options={[
          { value: 'plan', label: 'План' },
          { value: 'source', label: 'Источник', disabled: true },
          { value: 'scene', label: 'Перспектива' },
        ]}
        onChange={onChange}
      />,
    );
    const plan = screen.getByRole('button', { name: 'План' });
    const scene = screen.getByRole('button', { name: 'Перспектива' });
    plan.focus();
    fireEvent.keyDown(plan, { key: 'ArrowRight' });
    expect(scene).toHaveFocus();
    expect(plan).toHaveAttribute('aria-pressed', 'true');
    expect(scene).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(scene);
    expect(onChange).toHaveBeenCalledWith('scene');
    expect(plan).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Источник' })).toBeDisabled();
    fireEvent.keyDown(scene, { key: 'Home' });
    expect(plan).toHaveFocus();
  });
});
