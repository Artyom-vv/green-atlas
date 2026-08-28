import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RecommendationPanel } from './RecommendationPanel';

afterEach(cleanup);

describe('RecommendationPanel', () => {
  it('builds one proposal for several selected zones', () => {
    const onPreview = vi.fn();
    render(<RecommendationPanel zones={[
      { id: 'west', label: 'Запад', geometry: { type: 'Polygon', coordinates: [] } },
      { id: 'east', label: 'Восток', geometry: { type: 'Polygon', coordinates: [] } },
    ]} onPreview={onPreview} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Приоритет'), { target: { value: 'continuity' } });
    fireEvent.click(screen.getByRole('button', { name: 'Показать' }));
    expect(onPreview).toHaveBeenCalledWith(expect.objectContaining({ zone_ids: ['west', 'east'], profile: 'continuity', max_sites: 40 }));
  });
});
