import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { BrushToolPanel } from './BrushToolPanel';

afterEach(cleanup);

describe('BrushToolPanel', () => {
  it('keeps several add and subtract strokes in one preview', () => {
    const onPreview = vi.fn();
    const strokes = [
      { mode: 'add' as const, geometry: { type: 'LineString', coordinates: [[0, 0], [20, 0]] } },
      { mode: 'subtract' as const, geometry: { type: 'LineString', coordinates: [[5, 0], [8, 0]] } },
    ];
    render(<BrushToolPanel strokes={strokes} onPreview={onPreview} onClear={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText('Добавление: 1. Вычитание: 1.')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Состав кисти'), { target: { value: 'mixed' } });
    fireEvent.click(screen.getByRole('button', { name: 'Показать' }));
    expect(onPreview).toHaveBeenCalledWith(expect.objectContaining({ strokes, composition: 'mixed', spacing_m: 6, max_sites: 500 }));
  });
});
