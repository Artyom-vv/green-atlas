import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Button, TextInput, Toolbar } from '../index';

describe('Toolbar navigation', () => {
  it.each([
    ['horizontal', 'ArrowRight', 'ArrowLeft', 'ArrowDown'],
    ['vertical', 'ArrowDown', 'ArrowUp', 'ArrowRight'],
  ] as const)(
    'uses %s arrows only on buttons',
    (orientation, next, previous, ignored) => {
      render(
        <Toolbar orientation={orientation} label="Карта">
          <Button>Первый</Button>
          <Button disabled>Недоступный</Button>
          <Button>Последний</Button>
          <TextInput aria-label="Масштаб" defaultValue="100" />
        </Toolbar>,
      );
      const first = screen.getByRole('button', { name: 'Первый' });
      const last = screen.getByRole('button', { name: 'Последний' });
      expect(screen.getByRole('toolbar')).toHaveAttribute(
        'aria-orientation',
        orientation,
      );
      act(() => first.focus());
      fireEvent.keyDown(first, { key: ignored });
      expect(first).toHaveFocus();
      fireEvent.keyDown(first, { key: next });
      expect(last).toHaveFocus();
      fireEvent.keyDown(last, { key: previous });
      expect(first).toHaveFocus();
      fireEvent.keyDown(first, { key: 'End' });
      expect(last).toHaveFocus();
      fireEvent.keyDown(last, { key: 'Home' });
      expect(first).toHaveFocus();
      const input = screen.getByRole('textbox');
      act(() => input.focus());
      expect(fireEvent.keyDown(input, { key: next })).toBe(true);
      expect(fireEvent.keyDown(input, { key: 'Home' })).toBe(true);
      expect(input).toHaveFocus();
    },
  );
});
