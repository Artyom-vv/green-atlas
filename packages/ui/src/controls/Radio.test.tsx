import { fireEvent, render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { ControlProvider } from '../foundations/ControlProvider';
import { Radio } from './Radio';

describe('Radio', () => {
  it('preserves native group selection, ref, disabled state and separate descriptions', () => {
    const ref = createRef<HTMLInputElement>();
    const changed = vi.fn();
    render(
      <ControlProvider size="compact">
        <fieldset>
          <legend>Расположение</legend>
          <Radio
            ref={ref}
            name="location"
            value="perimeter"
            label="По периметру"
            defaultChecked
          />
          <Radio
            name="location"
            value="road"
            label="У проезда"
            description="Требуется геометрия проезда"
            disabled
            onChange={changed}
          />
          <Radio name="location" value="selected" label="На выделении" />
        </fieldset>
      </ControlProvider>,
    );
    const perimeter = screen.getByRole('radio', { name: 'По периметру' });
    expect(ref.current).toBe(perimeter);
    expect(perimeter).toBeChecked();
    const unavailable = screen.getByRole('radio', { name: 'У проезда' });
    expect(unavailable).toBeDisabled();
    expect(unavailable).toHaveAccessibleDescription(
      'Требуется геометрия проезда',
    );
    unavailable.click();
    expect(changed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('radio', { name: 'На выделении' }));
    expect(perimeter).not.toBeChecked();
    expect(screen.getByRole('radio', { name: 'На выделении' })).toBeChecked();
    expect(perimeter.closest('label')).toHaveAttribute('data-size', 'compact');
  });
});
