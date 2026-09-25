import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Dialog } from '@green/ui';
import { SpeciesAssignmentPanel } from '@/features/species-assignment/ui/SpeciesAssignmentPanel';

afterEach(cleanup);

const object = {
  id: 'tree-1',
  kind: 'tree' as const,
  x: 20,
  y: 20,
  radius: 1.6,
  status: 'warning' as const,
  size_class: 'unspecified' as const,
  spacing_policy: 'balanced' as const,
  locked: false,
};
const shortlist = [
  {
    status: 'review' as const,
    reasons: ['Широкая крона требует проверки'],
    species: {
      id: 'tilia@1',
      species_id: 'tilia',
      revision: 1,
      common_name: 'Липа мелколистная',
      scientific_name: 'Tilia cordata',
      kind: 'tree' as const,
      crown_shape: 'spreading' as const,
      mature_height_min_m: 18,
      mature_height_max_m: 25,
      mature_crown_diameter_min_m: 8,
      mature_crown_diameter_max_m: 14,
      growth_rate: 'moderate' as const,
      root_architecture: 'mixed' as const,
      provenance: 'native' as const,
      territory_policy: 'specialist_review' as const,
      risk_flags: ['broad_crown'],
      evidence_note: 'Диапазон',
      source_urls: ['https://example.test'],
      canopy_forecast: [],
      root_forecast: [],
    },
  },
];

describe('SpeciesAssignmentPanel', () => {
  it('offers an actionable kind choice for mixed selections', () => {
    const onSelectKind = vi.fn();
    render(
      <SpeciesAssignmentPanel
        objects={[object, { ...object, id: 'shrub-1', kind: 'shrub' }]}
        onSelectKind={onSelectKind}
        onAssign={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Кустарникам (1)' }));
    expect(onSelectKind).toHaveBeenCalledWith('shrub');
    expect(
      screen.queryByRole('button', { name: 'Проверить замену' }),
    ).not.toBeInTheDocument();
  });
  it('assigns one revision and size class through a preview action', () => {
    const onAssign = vi.fn();
    render(
      <SpeciesAssignmentPanel
        objects={[object]}
        shortlist={shortlist}
        onAssign={onAssign}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Сведения: Липа мелколистная' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    expect(
      screen.queryByLabelText('Поиск в каталоге пород'),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Выбрать другую породу' }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Проверить замену' }));
    expect(onAssign).toHaveBeenCalledWith('tilia@1', 'standard');
  });

  it('keeps query, selected revision and material while moving focus between catalog and details', async () => {
    const onAssign = vi.fn();
    const onClose = vi.fn();
    const onCatalogModeChange = vi.fn();
    render(
      <Dialog open stableHeight title="Назначить породу" onClose={onClose}>
        <SpeciesAssignmentPanel
          header={null}
          objects={[object]}
          shortlist={shortlist}
          onAssign={onAssign}
          onCancel={vi.fn()}
          onCatalogModeChange={onCatalogModeChange}
        />
      </Dialog>,
    );
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());
    expect(
      screen.getAllByRole('heading', { name: 'Назначить породу' }),
    ).toHaveLength(1);
    const search = screen.getByRole('textbox', {
      name: 'Поиск в каталоге пород',
    });
    fireEvent.change(search, { target: { value: ' TILIA ' } });
    const choice = screen.getByRole('button', {
      name: 'Сведения: Липа мелколистная',
    });
    const catalogViewport = choice.closest('[data-slot="scroll-viewport"]');
    expect(catalogViewport).not.toBeNull();
    expect(catalogViewport).not.toContainElement(search);
    expect(catalogViewport).not.toContainElement(
      screen.getByRole('button', { name: 'Отмена' }),
    );
    choice.focus();
    fireEvent.click(choice);
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    expect(
      screen.getByRole('heading', { name: 'Липа мелколистная' }),
    ).toHaveFocus();
    expect(onCatalogModeChange).toHaveBeenLastCalledWith(false);
    const detailsViewport = screen
      .getByRole('heading', { name: 'Липа мелколистная' })
      .closest('[data-slot="scroll-viewport"]');
    expect(detailsViewport).not.toBeNull();
    expect(detailsViewport).not.toContainElement(
      screen.getByRole('button', { name: 'Проверить замену' }),
    );
    fireEvent.change(
      screen.getByRole('combobox', { name: 'Посадочный материал' }),
      { target: { value: 'large' } },
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Выбрать другую породу' }),
    );
    const restoredSearch = screen.getByRole('textbox', {
      name: 'Поиск в каталоге пород',
    });
    expect(restoredSearch).toHaveFocus();
    expect(restoredSearch).toHaveValue(' TILIA ');
    expect(onCatalogModeChange).toHaveBeenLastCalledWith(true);
    expect(
      screen.getByRole('button', { name: 'Сведения: Липа мелколистная' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('status')).toHaveTextContent('Найдено 1');
    fireEvent.change(restoredSearch, { target: { value: 'нет такой породы' } });
    expect(
      screen.getByText('Растения не найдены'),
    ).toBeVisible();
    expect(screen.getByText('Липа мелколистная')).toBeVisible();
    fireEvent.change(restoredSearch, { target: { value: 'липа' } });
    fireEvent.click(
      screen.getByRole('button', { name: 'Сведения: Липа мелколистная' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    expect(
      screen.getByRole('heading', { name: 'Липа мелколистная' }),
    ).toHaveFocus();
    expect(
      screen.getByRole('combobox', { name: 'Посадочный материал' }),
    ).toHaveValue('large');
    fireEvent.click(screen.getByRole('button', { name: 'Проверить замену' }));
    expect(onAssign).toHaveBeenCalledExactlyOnceWith('tilia@1', 'large');
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('shows authoritative review conditions before collapsed dimensions and keeps source evidence accessible', () => {
    render(
      <SpeciesAssignmentPanel
        objects={[object]}
        shortlist={shortlist}
        onAssign={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Сведения: Липа мелколистная' }),
    ).toHaveAccessibleDescription('Tilia cordata Требует проверки');
    fireEvent.click(
      screen.getByRole('button', { name: 'Сведения: Липа мелколистная' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    const evidence = screen.getByRole('region', { name: 'Условия подбора' });
    expect(within(evidence).getByText('Требует проверки')).toBeVisible();
    expect(within(evidence).getByText(shortlist[0].reasons[0])).toBeVisible();
    expect(
      within(evidence).getByText(
        'Размещение и отступы проверим на следующем шаге.',
      ),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Размеры взрослого растения' }),
    ).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(
      screen.getByRole('button', { name: 'Данные о породе и источники' }),
    );
    expect(screen.getByText('Диапазон')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Источник 1' })).toHaveAttribute(
      'href',
      'https://example.test',
    );
  });

  it('uses API shortlist status without inferring it from species risk fields', () => {
    render(
      <SpeciesAssignmentPanel
        objects={[object]}
        shortlist={[
          {
            ...shortlist[0],
            status: 'available',
            reasons: ['Соответствует типу выбранных посадочных мест'],
          },
        ]}
        onAssign={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Сведения: Липа мелколистная' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    expect(screen.getByText('Подходит по типу')).toBeVisible();
    expect(screen.queryByText('Требует проверки')).not.toBeInTheDocument();
    expect(
      screen.getByText('Соответствует типу выбранных посадочных мест'),
    ).toBeVisible();
  });

  it('distinguishes request errors, loading, no available species and a search with no matches', () => {
    const props = { objects: [object], onAssign: vi.fn(), onCancel: vi.fn() };
    const { rerender } = render(
      <SpeciesAssignmentPanel {...props} error="Подборка не загрузилась" />,
    );
    expect(screen.getByText('Подборка не загрузилась')).toBeVisible();
    expect(
      screen.queryByText('Нет растений для выбора'),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    rerender(<SpeciesAssignmentPanel {...props} loading />);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Загружаем подборку для выбранных посадок',
    );
    rerender(<SpeciesAssignmentPanel {...props} shortlist={[]} />);
    expect(
      screen.getByText('Нет растений для выбора'),
    ).toBeVisible();
    expect(
      screen.queryByText('Растения не найдены'),
    ).not.toBeInTheDocument();
  });

  it('preserves preview, locked-object and loading guards while keeping the chosen material', () => {
    const props = {
      objects: [object],
      shortlist,
      onAssign: vi.fn(),
      onCancel: vi.fn(),
    };
    const { rerender } = render(<SpeciesAssignmentPanel {...props} />);
    fireEvent.click(
      screen.getByRole('button', { name: 'Сведения: Липа мелколистная' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    fireEvent.change(screen.getByRole('combobox'), {
      target: { value: 'sapling' },
    });
    rerender(<SpeciesAssignmentPanel {...props} previewing />);
    expect(screen.getByRole('combobox')).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Выбрать другую породу' }),
    ).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Отмена' })).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Проверить замену' }),
    ).toBeDisabled();
    rerender(
      <SpeciesAssignmentPanel
        {...props}
        objects={[{ ...object, locked: true }]}
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Проверить замену' }),
    ).toBeDisabled();
    rerender(<SpeciesAssignmentPanel {...props} loading />);
    expect(
      screen.getByRole('button', { name: 'Проверить замену' }),
    ).toBeDisabled();
    rerender(
      <SpeciesAssignmentPanel {...props} error="Ошибка обновления подборки" />,
    );
    expect(
      screen.getByRole('button', { name: 'Проверить замену' }),
    ).toBeDisabled();
    expect(screen.getByRole('combobox')).toHaveValue('sapling');
    expect(props.onAssign).not.toHaveBeenCalled();
  });
});
