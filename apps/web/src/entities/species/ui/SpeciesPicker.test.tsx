import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SpeciesRevision } from '@green/api-client';
import { SpeciesCatalog } from '@/entities/species/ui/SpeciesCatalog';
import { SpeciesPicker } from '@/entities/species/ui/SpeciesPicker';
import { speciesPhotos } from '@/entities/species/model/speciesPhotos';

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
  it('keeps dialog search outside the scrolling catalog and preserves empty-search recovery', async () => {
    const change = vi.fn();
    render(<SpeciesPicker species={species} onChange={change} />);
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать породу' }));
    const dialog = await screen.findByRole('dialog', { name: 'Каталог пород' });
    const viewport = dialog.querySelector('[data-slot="scroll-viewport"]');
    const search = screen.getByRole('textbox', {
      name: 'Поиск в каталоге пород',
    });
    const choice = screen.getByRole('button', {
      name: 'Выбрать: Рябина обыкновенная',
    });
    expect(viewport).not.toContainElement(search);
    expect(viewport).toContainElement(choice);
    fireEvent.change(search, { target: { value: 'нет такой породы' } });
    expect(viewport).toContainElement(
      screen.getByText('По этому запросу пород нет. Измените название.'),
    );
    expect(dialog.querySelector('[data-slot="scroll-viewport"]')).toBe(
      viewport,
    );
    fireEvent.change(search, { target: { value: 'SORBUS' } });
    fireEvent.click(
      screen.getByRole('button', { name: 'Выбрать: Рябина обыкновенная' }),
    );
    expect(change).toHaveBeenCalledExactlyOnceWith(sorbus.id);
  });

  it('keeps one managed lookup icon with or without a selected species photo', () => {
    const browse = vi.fn();
    const { rerender } = render(
      <SpeciesPicker species={species} onChange={vi.fn()} onBrowse={browse} />,
    );
    const button = screen.getByRole('button', { name: 'Выбрать породу' });
    const lookup = button.querySelector('svg');
    expect(button.querySelectorAll('svg')).toHaveLength(1);
    expect(lookup).toHaveAttribute('width', '16');
    expect(lookup).toHaveAttribute('height', '16');
    expect(lookup).toHaveAttribute('aria-hidden', 'true');
    fireEvent.click(button);
    expect(browse).toHaveBeenCalledOnce();
    rerender(
      <SpeciesPicker
        species={species}
        value={tilia.id}
        onChange={vi.fn()}
        onBrowse={browse}
      />,
    );
    expect(button).toHaveTextContent(tilia.common_name);
    expect(button.querySelector('img')).toHaveAttribute('alt');
    expect(button.querySelectorAll('svg')).toHaveLength(1);
  });
});

describe('placement species catalog', () => {
  it('keeps the standalone catalog in document flow regardless of card presentation', () => {
    const { container } = render(
      <SpeciesCatalog
        variant="placement"
        species={species}
        onChange={vi.fn()}
      />,
    );
    expect(container.querySelector('[data-slot="scroll-viewport"]')).toBeNull();
    expect(
      screen.getByRole('textbox', { name: 'Поиск в каталоге пород' }),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Выбрать: Рябина обыкновенная' }),
    ).toBeVisible();
  });

  it('keeps the chosen species visible while searching Russian or scientific names', () => {
    const change = vi.fn();
    render(
      <SpeciesCatalog
        variant="placement"
        species={species}
        value={tilia.id}
        onChange={change}
      />,
    );
    const search = screen.getByRole('textbox', {
      name: 'Поиск в каталоге пород',
    });
    fireEvent.change(search, { target: { value: ' SORBUS ' } });
    expect(screen.getByRole('status')).toHaveTextContent('Показано 1 из 2');
    expect(screen.getByText('Липа мелколистная')).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Выбрать: Рябина обыкновенная' }),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Выбрано: Липа мелколистная' }),
    ).not.toBeInTheDocument();
    fireEvent.change(search, { target: { value: 'ЛИПА' } });
    expect(
      screen.getByRole('button', { name: 'Выбрано: Липа мелколистная' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(change).not.toHaveBeenCalled();
  });

  it('restores results after an empty search without clearing the selected revision', () => {
    const change = vi.fn();
    render(
      <SpeciesCatalog
        variant="placement"
        species={species}
        value={tilia.id}
        onChange={change}
      />,
    );
    const search = screen.getByRole('textbox', {
      name: 'Поиск в каталоге пород',
    });
    fireEvent.change(search, { target: { value: 'неизвестная порода' } });
    expect(screen.getByText('Показано 0 из 2')).toBeVisible();
    expect(
      screen.getByText('По этому запросу пород нет. Измените название.'),
    ).toBeVisible();
    expect(screen.getByText('Липа мелколистная')).toBeVisible();
    fireEvent.change(search, { target: { value: '' } });
    expect(screen.getByRole('status')).toHaveTextContent('Показано 2 из 2');
    expect(
      screen.getByRole('button', { name: 'Выбрано: Липа мелколистная' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(change).not.toHaveBeenCalled();
  });

  it('offers focusable row buttons with species dimensions and preserves photo attribution', () => {
    const change = vi.fn();
    render(
      <SpeciesCatalog
        variant="placement"
        species={species}
        value={tilia.id}
        onChange={change}
      />,
    );
    const choice = screen.getByRole('button', {
      name: 'Выбрать: Рябина обыкновенная',
    });
    choice.focus();
    expect(choice).toHaveFocus();
    expect(choice).toHaveAccessibleDescription(
      'Sorbus aucuparia Высота 18–25 м Крона 8–14 м',
    );
    fireEvent.click(choice);
    expect(change).toHaveBeenCalledExactlyOnceWith(sorbus.id);
    fireEvent.click(
      screen.getByText('О фотографиях и источниках', { selector: 'summary' }),
    );
    expect(
      screen.getByRole('link', {
        name: speciesPhotos[sorbus.species_id].author,
      }),
    ).toHaveAttribute('href', speciesPhotos[sorbus.species_id].source);
  });

  it('blocks changes while disabled or refreshing the shortlist', () => {
    const change = vi.fn();
    const { rerender } = render(
      <SpeciesCatalog
        variant="placement"
        species={species}
        value={tilia.id}
        disabled
        onChange={change}
      />,
    );
    const choice = screen.getByRole('button', {
      name: 'Выбрать: Рябина обыкновенная',
    });
    expect(choice).toBeDisabled();
    fireEvent.click(choice);
    rerender(
      <SpeciesCatalog
        variant="placement"
        species={species}
        value={tilia.id}
        loading
        onChange={change}
      />,
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Загружаем породы для выбранных участков',
    );
    expect(
      screen.queryByRole('button', { name: 'Выбрать: Рябина обыкновенная' }),
    ).not.toBeInTheDocument();
    expect(change).not.toHaveBeenCalled();
  });
});
