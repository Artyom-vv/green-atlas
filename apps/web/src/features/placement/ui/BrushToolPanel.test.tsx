import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import type { BrushPreview } from '@green/api-client';
import { BrushToolPanel } from '@/features/placement/ui/BrushToolPanel';
import { useBrushForm } from '../model/useBrushForm';
import { toLiveBrushSettings } from '../model/brushForm';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const tree = {
  id: 'tree@1',
  species_id: 'tree',
  revision: 1,
  common_name: 'Липа',
  scientific_name: 'Tilia',
  kind: 'tree' as const,
  crown_shape: 'round' as const,
  mature_height_min_m: 10,
  mature_height_max_m: 20,
  mature_crown_diameter_min_m: 5,
  mature_crown_diameter_max_m: 8,
  growth_rate: 'moderate' as const,
  root_architecture: 'mixed' as const,
  provenance: 'native' as const,
  territory_policy: 'general_draft' as const,
  risk_flags: [],
  evidence_note: 'test',
  source_urls: ['https://example.test'],
};
const baseProps = (): ComponentProps<typeof BrushToolPanel> => ({
  strokes: [],
  zones: [
    {
      id: 'work',
      label: 'Рабочий участок',
      geometry: { type: 'Polygon', coordinates: [] },
    },
  ],
  zoneIds: ['work'],
  species: [tree],
  width: 12,
  operation: 'add',
  onZoneIdsChange: vi.fn(),
  onWidth: vi.fn(),
  onOperation: vi.fn(),
  onPreview: vi.fn(),
  onApply: vi.fn(),
  onClear: vi.fn(),
  onCancel: vi.fn(),
});

describe('BrushToolPanel', () => {
  it('keeps the external brush draft across panel teardown and cancels a queued preview', () => {
    vi.useFakeTimers();
    const owner = renderHook(() => useBrushForm({ treeSpeciesId: tree.id }));
    const form = owner.result.current;
    const props = {
      ...baseProps(),
      form,
      strokes: [
        {
          mode: 'add' as const,
          geometry: {
            type: 'LineString',
            coordinates: [
              [0, 0],
              [20, 0],
            ],
          },
        },
      ],
    };
    const observed = vi.fn();
    const unsubscribe = form.subscribe({
      formState: { values: true },
      callback: ({ values }) => observed(toLiveBrushSettings(values)),
    });
    const panel = render(<BrushToolPanel {...props} />);
    fireEvent.change(screen.getByLabelText('Плотность кисти'), {
      target: { value: 'dense' },
    });
    fireEvent.change(screen.getByLabelText('Шаг кисти'), {
      target: { value: '3.5' },
    });
    expect(observed).toHaveBeenLastCalledWith(
      expect.objectContaining({ density: 'dense', spacing: 3.5 }),
    );
    act(() => vi.advanceTimersByTime(50));
    panel.unmount();
    act(() => vi.advanceTimersByTime(100));
    expect(props.onPreview).not.toHaveBeenCalled();
    observed.mockClear();
    render(<BrushToolPanel {...props} />);
    expect(screen.getByLabelText('Плотность кисти')).toHaveValue('dense');
    expect(screen.getByLabelText('Шаг кисти')).toHaveValue(3.5);
    expect(observed).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(100));
    expect(props.onPreview).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ density: 'dense', spacing_m: 3.5 }),
    );
    unsubscribe();
  });

  it('keeps several add and subtract strokes in one preview', async () => {
    const onPreview = vi.fn();
    const strokes = [
      {
        mode: 'add' as const,
        geometry: {
          type: 'LineString',
          coordinates: [
            [0, 0],
            [20, 0],
          ],
        },
      },
      {
        mode: 'subtract' as const,
        geometry: {
          type: 'LineString',
          coordinates: [
            [5, 0],
            [8, 0],
          ],
        },
      },
    ];
    render(
      <BrushToolPanel
        strokes={strokes}
        zones={[
          {
            id: 'work',
            label: 'Рабочий участок',
            geometry: { type: 'Polygon', coordinates: [] },
          },
        ]}
        zoneIds={['work']}
        width={12}
        operation="add"
        onZoneIdsChange={vi.fn()}
        onWidth={vi.fn()}
        onOperation={vi.fn()}
        onPreview={onPreview}
        onApply={vi.fn()}
        onClear={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText('Мазки: 2'));
    expect(screen.getByText('добавить 1, убрать 1')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Состав кисти'), {
      target: { value: 'mixed' },
    });
    await waitFor(() =>
      expect(onPreview).toHaveBeenCalledWith(
        expect.objectContaining({
          zone_ids: ['work'],
          strokes,
          composition: 'mixed',
          spacing_m: 4,
          max_sites: 500,
        }),
      ),
    );
  });

  it('keeps one density control in the primary brush flow', () => {
    render(
      <BrushToolPanel
        strokes={[]}
        zones={[]}
        zoneIds={[]}
        width={12}
        operation="add"
        onZoneIdsChange={vi.fn()}
        onWidth={vi.fn()}
        onOperation={vi.fn()}
        onPreview={vi.fn()}
        onApply={vi.fn()}
        onClear={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(
      screen.queryByText('Дополнительные настройки'),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Кисть' })).toBeVisible();
    expect(screen.getByRole('group', { name: 'Посадки' })).toBeVisible();
    expect(screen.getByLabelText('Плотность кисти')).toBeVisible();
  });

  it('guides the user to select a zone before enabling brush controls', () => {
    render(
      <BrushToolPanel
        strokes={[]}
        zones={[]}
        zoneIds={[]}
        width={12}
        operation="add"
        onZoneIdsChange={vi.fn()}
        onWidth={vi.fn()}
        onOperation={vi.fn()}
        onPreview={vi.fn()}
        onApply={vi.fn()}
        onClear={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(
      screen.getByText('Выберите участок', { selector: 'strong' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('Рисуйте по участку')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Режим кисти')).toBeDisabled();
    expect(screen.getByLabelText('Состав кисти')).toBeDisabled();
    expect(
      screen.getByRole('spinbutton', { name: 'Диаметр кисти' }),
    ).toBeDisabled();
    expect(screen.getByLabelText('Плотность кисти')).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: /^Добавить/ }),
    ).not.toBeInTheDocument();
  });

  it('switches the guide to drawing after a zone is selected', () => {
    render(
      <BrushToolPanel
        strokes={[]}
        zones={[
          {
            id: 'work',
            label: 'Рабочий участок',
            geometry: { type: 'Polygon', coordinates: [] },
          },
        ]}
        zoneIds={['work']}
        width={12}
        operation="add"
        onZoneIdsChange={vi.fn()}
        onWidth={vi.fn()}
        onOperation={vi.fn()}
        onPreview={vi.fn()}
        onApply={vi.fn()}
        onClear={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText('Рисуйте по участку')).toBeInTheDocument();
    expect(screen.getByLabelText('Режим кисти')).toBeEnabled();
    expect(
      screen.getByRole('spinbutton', { name: 'Диаметр кисти' }),
    ).toBeEnabled();
  });

  it('asks for the missing species without inviting drawing and still lets the user cancel', () => {
    const props = baseProps();
    render(<BrushToolPanel {...props} />);
    expect(
      screen.getByText(
        'Выберите породу перед рисованием',
      ),
    ).toBeVisible();
    expect(screen.queryByText('Рисуйте по участку')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(props.onCancel).toHaveBeenCalledOnce();
    expect(props.onPreview).not.toHaveBeenCalled();
  });

  it('allows a subtract-only gesture without a species and forwards its exact contour', () => {
    vi.useFakeTimers();
    const props = { ...baseProps(), operation: 'subtract' as const };
    const { rerender } = render(<BrushToolPanel {...props} />);
    expect(screen.getByText('Рисуйте по участку')).toBeVisible();
    expect(
      screen.queryByText(/Выберите породу перед рисованием/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('group', { name: 'Посадки' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Плотность кисти')).not.toBeInTheDocument();
    const strokes = [
      {
        mode: 'subtract' as const,
        geometry: {
          type: 'LineString',
          coordinates: [
            [5.2, 8],
            [15.1, 3],
          ],
        },
      },
    ];
    rerender(<BrushToolPanel {...props} strokes={strokes} />);
    act(() => vi.advanceTimersByTime(100));
    expect(props.onPreview).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({
        zone_ids: ['work'],
        strokes,
        width_m: 12,
        spacing_m: 6,
        seed: 47,
        max_sites: 500,
      }),
    );
  });

  it('keeps addition settings while erasing mixed strokes and restores them after a subtract-only view', () => {
    vi.useFakeTimers();
    const shrub = {
      ...tree,
      id: 'shrub@1',
      kind: 'shrub' as const,
      common_name: 'Дёрен',
    };
    const add = {
      mode: 'add' as const,
      geometry: {
        type: 'LineString',
        coordinates: [
          [0, 0],
          [20, 0],
        ],
      },
    };
    const subtract = {
      mode: 'subtract' as const,
      geometry: {
        type: 'LineString',
        coordinates: [
          [5, 0],
          [8, 0],
        ],
      },
    };
    const owner = renderHook(() =>
      useBrushForm({
        density: 'balanced',
        composition: 'mixed',
        treeShare: 0.7,
        spacing: 4,
        treeSpeciesId: tree.id,
        shrubSpeciesId: shrub.id,
      }),
    );
    const props = {
      ...baseProps(),
      species: [tree, shrub],
      operation: 'subtract' as const,
      form: owner.result.current,
    };
    const { rerender } = render(
      <BrushToolPanel {...props} strokes={[add, subtract]} />,
    );
    expect(
      screen.getByRole('button', { name: 'Изменить растение в каталоге: Порода деревьев для кисти' }).parentElement,
    ).toHaveTextContent('Липа');
    expect(
      screen.getByRole('button', { name: 'Изменить растение в каталоге: Порода кустарников для кисти' }).parentElement,
    ).toHaveTextContent('Дёрен');
    fireEvent.change(screen.getByLabelText('Доля деревьев'), {
      target: { value: '40' },
    });
    fireEvent.change(screen.getByLabelText('Шаг кисти'), {
      target: { value: '3.5' },
    });
    fireEvent.change(screen.getByLabelText('Плотность кисти'), {
      target: { value: 'dense' },
    });
    act(() => vi.advanceTimersByTime(100));
    expect(props.onPreview).toHaveBeenLastCalledWith(
      expect.objectContaining({
        strokes: [add, subtract],
        composition: 'mixed',
        tree_share: 0.4,
        spacing_m: 3.5,
        density: 'dense',
        tree_species_revision_id: tree.id,
        shrub_species_revision_id: shrub.id,
      }),
    );

    rerender(<BrushToolPanel {...props} strokes={[subtract]} />);
    expect(
      screen.queryByRole('group', { name: 'Посадки' }),
    ).not.toBeInTheDocument();
    rerender(
      <BrushToolPanel {...props} operation="add" strokes={[subtract]} />,
    );
    expect(screen.getByLabelText('Доля деревьев')).toHaveValue(40);
    expect(screen.getByLabelText('Шаг кисти')).toHaveValue(3.5);
    expect(screen.getByLabelText('Плотность кисти')).toHaveValue('dense');
    expect(
      screen.getByRole('button', { name: 'Изменить растение в каталоге: Порода кустарников для кисти' }).parentElement,
    ).toHaveTextContent('Дёрен');
  });

  it('requires species for earlier add strokes even when the current brush operation subtracts', () => {
    vi.useFakeTimers();
    const props = {
      ...baseProps(),
      operation: 'subtract' as const,
      strokes: [
        {
          mode: 'add' as const,
          geometry: {
            type: 'LineString',
            coordinates: [
              [0, 0],
              [20, 0],
            ],
          },
        },
      ],
    };
    render(<BrushToolPanel {...props} />);
    expect(
      screen.getByText(
        'Выберите породу для добавляющих мазков',
      ),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Открыть каталог растений: Порода деревьев для кисти' }),
    ).toBeEnabled();
    act(() => vi.advanceTimersByTime(200));
    expect(props.onPreview).not.toHaveBeenCalled();
  });

  it('cancels a queued preview when applying begins and blocks edits until the write finishes', () => {
    vi.useFakeTimers();
    const props = {
      ...baseProps(),
      species: undefined,
      strokes: [
        {
          mode: 'add' as const,
          geometry: {
            type: 'LineString',
            coordinates: [
              [0, 0],
              [20, 0],
            ],
          },
        },
      ],
    };
    const { rerender } = render(<BrushToolPanel {...props} />);
    act(() => vi.advanceTimersByTime(50));
    rerender(<BrushToolPanel {...props} applying />);
    act(() => vi.advanceTimersByTime(200));
    expect(props.onPreview).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Режим кисти')).toBeDisabled();
    expect(screen.getByLabelText('Состав кисти')).toBeDisabled();
    expect(
      screen.getByRole('spinbutton', { name: 'Диаметр кисти' }),
    ).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Отмена' })).toBeDisabled();
    fireEvent.click(screen.getByText('Мазки: 1'));
    expect(
      screen.getByRole('button', { name: 'Очистить мазки' }),
    ).toBeDisabled();
    rerender(<BrushToolPanel {...props} strokes={[]} />);
    act(() => vi.advanceTimersByTime(200));
    expect(props.onPreview).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Режим кисти')).toBeEnabled();
  });

  it('requires a current applicable result and keeps all strokes while awaiting review', () => {
    const props = {
      ...baseProps(),
      species: undefined,
      strokes: [
        {
          mode: 'add' as const,
          geometry: {
            type: 'LineString',
            coordinates: [
              [0, 0],
              [20, 0],
            ],
          },
        },
        {
          mode: 'subtract' as const,
          geometry: {
            type: 'LineString',
            coordinates: [
              [4, 0],
              [8, 0],
            ],
          },
        },
      ],
    };
    const preview: BrushPreview = {
      brush_id: 'brush-1',
      requested_count: 5,
      accepted_count: 4,
      added_count: 4,
      removed_count: 1,
      skipped: [],
      change_set: {
        id: 'change-1',
        digest: 'digest',
        base_plan_version: 1,
        source: 'pattern',
        label: 'Кисть',
        can_apply: true,
        additions: [],
        updates: [],
        deletion_ids: [],
        candidate_results: [],
        expires_at: '2026-10-01T00:00:00Z',
      },
    };
    const { rerender } = render(
      <BrushToolPanel {...props} preview={preview} loading />,
    );
    expect(screen.getByRole('button', { name: 'Добавить 4' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Добавить 4' }));
    expect(props.onApply).not.toHaveBeenCalled();
    rerender(
      <BrushToolPanel
        {...props}
        preview={{
          ...preview,
          change_set: { ...preview.change_set!, can_apply: false },
        }}
      />,
    );
    expect(screen.getByRole('button', { name: 'Добавить 4' })).toBeDisabled();
    rerender(<BrushToolPanel {...props} preview={preview} />);
    fireEvent.click(screen.getByRole('button', { name: 'Добавить 4' }));
    expect(props.onApply).toHaveBeenCalledOnce();
    expect(props.onClear).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Мазки: 2'));
    expect(screen.getByText('добавить 1, убрать 1')).toBeVisible();
    expect(screen.getByText('К удалению: 1')).toBeVisible();
  });
});
