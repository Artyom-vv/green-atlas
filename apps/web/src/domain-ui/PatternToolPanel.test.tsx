import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PatternToolPanel } from './PatternToolPanel';

afterEach(cleanup);

const zones = [
  { id: 'west', label: 'Западный участок', geometry: { type: 'Polygon', coordinates: [] } },
  { id: 'east', label: 'Восточный участок', geometry: { type: 'Polygon', coordinates: [] } },
];

describe('PatternToolPanel', () => {
  it('previews a fill across several selected zones', () => {
    const onPreview = vi.fn();
    render(<PatternToolPanel mode="fill" zones={zones} onPreview={onPreview} onCancel={vi.fn()} />);

    expect(screen.getByRole('checkbox', { name: 'Западный участок' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Восточный участок' })).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Показать' }));

    expect(onPreview).toHaveBeenCalledWith(expect.objectContaining({
      type: 'fill',
      zone_ids: ['west', 'east'],
      layout: 'staggered',
      spacing_m: 6,
    }));
  });

  it('requires an axis before previewing a row', () => {
    const { rerender } = render(<PatternToolPanel mode="row" zones={zones} onPreview={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Показать' })).toBeDisabled();

    rerender(<PatternToolPanel mode="row" zones={zones} axis={{ type: 'LineString', coordinates: [[0, 0], [10, 0]] }} onPreview={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Показать' })).toBeEnabled();
  });
});
