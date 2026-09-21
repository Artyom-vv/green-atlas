import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SpeciesRevision } from '@green/api-client';
import {
  PlantingCompositionFields,
  type PlantingCompositionFieldsProps,
} from '@/entities/species/ui/PlantingCompositionFields';

afterEach(cleanup);
const tree: SpeciesRevision = {
  id: 'tree@1',
  species_id: 'tree',
  revision: 1,
  common_name: 'Липа',
  scientific_name: 'Tilia',
  kind: 'tree',
  crown_shape: 'round',
  mature_height_min_m: 10,
  mature_height_max_m: 20,
  mature_crown_diameter_min_m: 5,
  mature_crown_diameter_max_m: 8,
  growth_rate: 'moderate',
  root_architecture: 'mixed',
  provenance: 'native',
  territory_policy: 'general_draft',
  risk_flags: [],
  evidence_note: 'test',
  source_urls: ['https://example.test'],
};
const shrub: SpeciesRevision = {
  ...tree,
  id: 'shrub@1',
  species_id: 'shrub',
  common_name: 'Сирень',
  scientific_name: 'Syringa',
  kind: 'shrub',
};
const base = (): PlantingCompositionFieldsProps => ({
  composition: 'trees',
  onCompositionChange: vi.fn(),
  species: [tree, shrub],
  treeSpeciesId: tree.id,
  shrubSpeciesId: shrub.id,
  onTreeSpeciesChange: vi.fn(),
  onShrubSpeciesChange: vi.fn(),
});

describe('PlantingCompositionFields', () => {
  it('delegates composition changes and retains owner values when kinds become visible again', () => {
    const props = base();
    const { rerender } = render(<PlantingCompositionFields {...props} />);
    fireEvent.change(screen.getByRole('combobox', { name: 'Состав посадок' }), {
      target: { value: 'shrubs' },
    });
    expect(props.onCompositionChange).toHaveBeenCalledWith('shrubs');
    expect(
      screen.getByRole('button', { name: 'Порода деревьев' }),
    ).toHaveTextContent('Липа');
    rerender(<PlantingCompositionFields {...props} composition="shrubs" />);
    expect(
      screen.queryByRole('button', { name: 'Порода деревьев' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Порода кустарников' }),
    ).toHaveTextContent('Сирень');
    rerender(<PlantingCompositionFields {...props} composition="mixed" />);
    expect(
      screen.getByRole('button', { name: 'Порода деревьев' }),
    ).toHaveTextContent('Липа');
    expect(
      screen.getByRole('button', { name: 'Порода кустарников' }),
    ).toHaveTextContent('Сирень');
    expect(props.onTreeSpeciesChange).not.toHaveBeenCalled();
    expect(props.onShrubSpeciesChange).not.toHaveBeenCalled();
  });

  it('opens the existing modal catalog filtered by kind and returns selection to its caller', () => {
    const props = base();
    render(<PlantingCompositionFields {...props} composition="mixed" />);
    fireEvent.click(screen.getByRole('button', { name: 'Порода кустарников' }));
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(
      screen.queryByRole('button', { name: /Липа/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Сведения: Сирень' }));
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать растение' }));
    expect(props.onShrubSpeciesChange).toHaveBeenCalledWith(shrub.id);
    expect(props.onTreeSpeciesChange).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders the share only when mixed composition has an editable share and keeps bounds local until blur', () => {
    const props = base();
    const onTreeShareChange = vi.fn();
    const { rerender } = render(
      <PlantingCompositionFields {...props} composition="mixed" />,
    );
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    rerender(
      <PlantingCompositionFields
        {...props}
        composition="mixed"
        treeSharePercent={70}
        onTreeShareChange={onTreeShareChange}
      />,
    );
    const share = screen.getByRole('spinbutton', { name: 'Доля деревьев' });
    fireEvent.change(share, { target: { value: '120' } });
    expect(onTreeShareChange).not.toHaveBeenCalled();
    fireEvent.blur(share);
    expect(onTreeShareChange).toHaveBeenCalledWith(100);
    rerender(
      <PlantingCompositionFields
        {...props}
        treeSharePercent={70}
        onTreeShareChange={onTreeShareChange}
      />,
    );
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
  });

  it('preserves caller labels, browse destination and loading/disabled controls', () => {
    const props = base();
    const onBrowseTree = vi.fn();
    const { rerender } = render(
      <PlantingCompositionFields
        {...props}
        allowMixed={false}
        onBrowseTree={onBrowseTree}
        labels={{
          compositionAria: 'Состав группы',
          tree: 'Порода',
          treeAria: 'Порода для участка',
        }}
      />,
    );
    expect(
      screen.queryByRole('option', { name: 'Смешанный' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('combobox', { name: 'Состав группы' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Порода для участка' }));
    expect(onBrowseTree).toHaveBeenCalledOnce();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    rerender(
      <PlantingCompositionFields
        {...props}
        composition="mixed"
        treeSpeciesDisabled
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Порода деревьев' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Порода кустарников' }),
    ).toBeEnabled();
    rerender(
      <PlantingCompositionFields
        {...props}
        composition="mixed"
        disabled
        treeSharePercent={70}
        onTreeShareChange={vi.fn()}
      />,
    );
    expect(screen.getByRole('combobox')).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Порода деревьев' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Порода кустарников' }),
    ).toBeDisabled();
    expect(screen.getByRole('spinbutton')).toBeDisabled();
  });

  it('distinguishes omitted species UI from an explicitly empty selectable catalog', () => {
    const props = base();
    const { rerender } = render(
      <PlantingCompositionFields {...props} showSpecies={false} />,
    );
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    rerender(<PlantingCompositionFields {...props} species={[]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Порода деревьев' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(
      screen.getByText('Породы для выбора пока недоступны.'),
    ).toBeVisible();
  });
});
