import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SpeciesRevision } from '@green/api-client';
import { SpeciesCatalog } from '@/entities/species/ui/SpeciesCatalog';
import { SpeciesPicker } from '@/entities/species/ui/SpeciesPicker';
import { speciesPhotos } from '@/entities/species/model/speciesPhotos';
import { PlantCatalogContext } from '@/entities/species/model/PlantCatalogContext';

afterEach(cleanup);

const tilia: SpeciesRevision = {
  id: 'tilia@1',
  species_id: 'tilia-cordata',
  revision: 1,
  common_name: 'Липа мелколистная',
  scientific_name: 'Tilia cordata',
  kind: 'tree',
  crown_shape: 'spreading',
  mature_height_min_m: 18,
  mature_height_max_m: 25,
  mature_crown_diameter_min_m: 8,
  mature_crown_diameter_max_m: 14,
  growth_rate: 'moderate',
  root_architecture: 'mixed',
  provenance: 'native',
  territory_policy: 'general_draft',
  risk_flags: [],
  evidence_note: 'test',
  source_urls: [],
};
const sorbus = {
  ...tilia,
  id: 'sorbus@1',
  species_id: 'sorbus-aucuparia',
  common_name: 'Рябина обыкновенная',
  scientific_name: 'Sorbus aucuparia',
};
const species = [sorbus, tilia];

describe('SpeciesPicker', () => {
  it('shows reference matrix values without turning an unreviewed row into an assignable model', () => {
    const change = vi.fn();
    const inventory = {
      revision: 'test',
      source_url: 'https://example.org/source.pdf',
      source_sha256: 'a'.repeat(64),
      categories: ['courtyard', 'preschool'],
      special_territories_note: 'Проверка режима',
      entries: [
        {
          id: 'reference',
          name: 'Ель сербская',
          source_name: 'Ель сербская',
          tier: 'main' as const,
          section: 'Хвойные деревья',
          kind: 'tree' as const,
          page: 1,
          row: 1,
          cells: '+-',
          matrix_reviewed: false,
          conditions_reviewed: false,
          requires_spread_control: false,
        },
      ],
    };
    render(
      <PlantCatalogContext.Provider value={{ inventory }}>
        <SpeciesCatalog species={species} onChange={change} />
      </PlantCatalogContext.Provider>,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Весь ассортимент · 1' }),
    );
    expect(screen.getByRole('heading', { name: 'Ель сербская' })).toBeVisible();
    expect(screen.getByText('Да')).toBeVisible();
    expect(screen.getByText('Нет')).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Выбрать растение' }),
    ).toBeDisabled();
    expect(change).not.toHaveBeenCalled();
  });
  it('keeps search and confirmation fixed, browsing does not assign until confirmed', async () => {
    const change = vi.fn();
    render(<SpeciesPicker species={species} onChange={change} />);
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать породу' }));
    const dialog = await screen.findByRole('dialog');
    const search = screen.getByRole('textbox', {
      name: 'Поиск в каталоге пород',
    });
    const list = dialog.querySelector('[data-slot="scroll-viewport"]');
    const confirm = screen.getByRole('button', { name: 'Выбрать растение' });
    expect(list).not.toContainElement(search);
    expect(list).not.toContainElement(confirm);
    fireEvent.change(search, { target: { value: 'нет такой породы' } });
    expect(confirm).toBeDisabled();
    expect(
      screen.getByText('По этому запросу пород нет. Измените название.'),
    ).toBeVisible();
    fireEvent.change(search, { target: { value: 'SORBUS' } });
    fireEvent.click(
      screen.getByRole('button', { name: 'Сведения: Рябина обыкновенная' }),
    );
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(confirm);
    expect(change).toHaveBeenCalledExactlyOnceWith(sorbus.id);
  });

  it('shows characteristics and credits of the focused plant without replacing the current choice', () => {
    const change = vi.fn();
    render(
      <SpeciesCatalog species={species} value={tilia.id} onChange={change} />,
    );
    const row = screen.getByRole('button', {
      name: 'Сведения: Рябина обыкновенная',
    });
    row.focus();
    expect(row).toHaveFocus();
    fireEvent.click(row);
    expect(
      screen.getByRole('heading', { name: sorbus.common_name }),
    ).toBeVisible();
    expect(screen.getByText('Диаметр кроны')).toBeVisible();
    expect(
      screen.getByRole('link', {
        name: speciesPhotos[sorbus.species_id].author,
      }),
    ).toHaveAttribute('href', speciesPhotos[sorbus.species_id].source);
    expect(change).not.toHaveBeenCalled();
  });

  it('allows inspecting restricted plants but cannot confirm them', () => {
    const change = vi.fn();
    render(
      <SpeciesCatalog
        species={species}
        onChange={change}
        itemStatuses={{
          [sorbus.id]: {
            label: 'Не рекомендована для детского сада',
            tone: 'warning',
            canSelect: false,
          },
        }}
      />,
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Сведения: Рябина обыкновенная' }),
    );
    expect(
      screen.getByText('Не рекомендована для детского сада'),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Сведения: Липа мелколистная' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    expect(change).toHaveBeenCalledExactlyOnceWith(tilia.id);
  });

  it('blocks confirmation during refresh and keeps one lookup icon', () => {
    const change = vi.fn();
    const { rerender } = render(
      <SpeciesPicker species={species} onChange={change} />,
    );
    expect(screen.getByRole('button').querySelectorAll('svg')).toHaveLength(1);
    rerender(<SpeciesCatalog species={species} loading onChange={change} />);
    expect(
      screen.getByRole('button', { name: 'Выбрать растение' }),
    ).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Сведения: Липа мелколистная' }),
    ).not.toBeInTheDocument();
    expect(change).not.toHaveBeenCalled();
  });
});
