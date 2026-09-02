import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { BrushToolPanel } from './BrushToolPanel';

afterEach(cleanup);

describe('BrushToolPanel', () => {
  it('keeps several add and subtract strokes in one preview', async () => {
    const onPreview = vi.fn();
    const strokes = [
      { mode: 'add' as const, geometry: { type: 'LineString', coordinates: [[0, 0], [20, 0]] } },
      { mode: 'subtract' as const, geometry: { type: 'LineString', coordinates: [[5, 0], [8, 0]] } },
    ];
    render(<BrushToolPanel strokes={strokes} zones={[{ id: 'work', label: 'Рабочий участок', geometry: { type: 'Polygon', coordinates: [] } }]} zoneIds={['work']} width={12} operation="add" onZoneIdsChange={vi.fn()} onWidth={vi.fn()} onOperation={vi.fn()} onPreview={onPreview} onApply={vi.fn()} onClear={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText('добавить 1, убрать 1')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Состав кисти'), { target: { value: 'mixed' } });
    await waitFor(() => expect(onPreview).toHaveBeenCalledWith(expect.objectContaining({ zone_ids: ['work'], strokes, composition: 'mixed', spacing_m: 4, max_sites: 500 })));
  });

  it('keeps one density control in the primary brush flow', () => {
    render(<BrushToolPanel strokes={[]} zones={[]} zoneIds={[]} width={12} operation="add" onZoneIdsChange={vi.fn()} onWidth={vi.fn()} onOperation={vi.fn()} onPreview={vi.fn()} onApply={vi.fn()} onClear={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.queryByText('Дополнительные настройки')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Плотность кисти')).toBeVisible();
  });
});
