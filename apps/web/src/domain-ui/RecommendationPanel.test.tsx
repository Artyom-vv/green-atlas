import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RecommendationPanel } from './RecommendationPanel';

afterEach(cleanup);

describe('RecommendationPanel', () => {
  const zones = [{ id: 'west', label: 'Запад', geometry: { type: 'Polygon' as const, coordinates: [] } }];
  it('asks one spatial question for buildings, not priority or quantity, and keeps the task on back', async () => {
    const onScreenPreview = vi.fn();
    const onScreenMode = vi.fn();
    const onInterpret = vi.fn().mockResolvedValue({ arrangement: 'building_screen', profile: null, max_sites: null, unsupported: [], questions: [] });
    render(<RecommendationPanel guided zones={zones} onPreview={vi.fn()} onCancel={vi.fn()} onInterpret={onInterpret}
      onScreenMode={onScreenMode} onScreenPreview={onScreenPreview} screenTargets={{ geometry: { type: 'Polygon', coordinates: [] }, has_roads: true }} />);
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать задачу' }));
    fireEvent.change(screen.getByLabelText('Задача озеленения'), { target: { value: 'Группы деревьев вдоль зданий' } });
    fireEvent.click(screen.getByRole('button', { name: 'Разобрать задачу' }));
    await screen.findByText('С какой стороны прикрыть здания?');
    expect(screen.queryByLabelText('Приоритет')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Максимум посадок')).not.toBeInTheDocument();
    expect(onScreenMode).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByRole('radio', { name: 'Со стороны проездов' }));
    fireEvent.click(screen.getByRole('button', { name: 'Показать на карте' }));
    expect(onScreenPreview).toHaveBeenCalledWith({ zone_ids: ['west'], screen_side: 'roads', max_sites: null });
    fireEvent.click(screen.getByRole('button', { name: 'Назад' }));
    expect(screen.getByLabelText('Задача озеленения')).toHaveValue('Группы деревьев вдоль зданий');
    expect(onScreenMode).toHaveBeenLastCalledWith(false);
  });
  it('reviews interpreted parameters before calling the placement engine', async () => {
    const onPreview = vi.fn();
    const onInterpret = vi.fn().mockResolvedValue({ profile: 'shade', max_sites: 25, unsupported: [], questions: [] });
    render(<RecommendationPanel guided zones={zones} onPreview={onPreview} onCancel={vi.fn()} onInterpret={onInterpret} />);
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать задачу' }));
    fireEvent.change(screen.getByLabelText('Задача озеленения'), { target: { value: 'Больше тени, максимум 25 посадок' } });
    fireEvent.click(screen.getByRole('button', { name: 'Разобрать задачу' }));
    await screen.findByText('Проверьте параметры');
    expect(onPreview).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Приоритет')).toHaveValue('shade');
    expect(screen.getByLabelText('Максимум посадок')).toHaveValue(25);
    fireEvent.click(screen.getByRole('button', { name: 'Проверить места' }));
    expect(onPreview).toHaveBeenCalledWith({ profile: 'shade', max_sites: 25, zone_ids: ['west'] });
  });
  it('does not silently drop unsupported requirements or invent missing parameters', async () => {
    const onPreview = vi.fn();
    const onInterpret = vi.fn().mockResolvedValue({ profile: null, max_sites: null, unsupported: ['Точная порода: липа'], questions: [] });
    render(<RecommendationPanel guided zones={zones} onPreview={onPreview} onCancel={vi.fn()} onInterpret={onInterpret} />);
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать задачу' }));
    fireEvent.change(screen.getByLabelText('Задача озеленения'), { target: { value: 'Посади липы' } });
    fireEvent.click(screen.getByRole('button', { name: 'Разобрать задачу' }));
    await screen.findByText('Точная порода: липа');
    expect(screen.queryByRole('button', { name: 'Проверить места' })).not.toBeInTheDocument();
    expect(onPreview).not.toHaveBeenCalled();
  });
  it('ignores a late response after the user switches to manual entry', async () => {
    let resolve!: (value: { arrangement: 'area'; profile: 'shade'; max_sites: number; unsupported: string[]; questions: string[] }) => void;
    const onInterpret = vi.fn(() => new Promise<{ arrangement: 'area'; profile: 'shade'; max_sites: number; unsupported: string[]; questions: string[] }>(done => { resolve = done; }));
    render(<RecommendationPanel zones={zones} onPreview={vi.fn()} onCancel={vi.fn()} onInterpret={onInterpret} />);
    fireEvent.change(screen.getByLabelText('Задача озеленения'), { target: { value: 'Больше тени' } });
    fireEvent.click(screen.getByRole('button', { name: 'Разобрать задачу' }));
    fireEvent.click(screen.getByRole('button', { name: 'Настроить вручную' }));
    resolve({ arrangement: 'area', profile: 'shade', max_sites: 12, unsupported: [], questions: [] });
    await waitFor(() => expect(screen.getByLabelText('Приоритет')).toHaveValue('balanced'));
    expect(screen.queryByText('Проверьте параметры')).not.toBeInTheDocument();
  });
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
