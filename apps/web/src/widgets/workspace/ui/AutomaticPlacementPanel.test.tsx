import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AutomaticPlacementPreview } from '@green/api-client';
import type { WorkspaceRecommendationToolProps } from './WorkspaceRecommendationTool.props';
import { AutomaticPlacementPanel } from './AutomaticPlacementPanel';

afterEach(cleanup);

const props = () => ({
  applyChanges: { mutate: vi.fn(), isPending: false, error: undefined },
  automaticPreview: undefined as AutomaticPlacementPreview | undefined,
  previewAutomatic: {
    mutate: vi.fn(), reset: vi.fn(), isPending: false, error: undefined,
  },
  project: {
    plan: { version: 7 },
    planting_zones: [{ id: 'zone-1', label: 'Участок 1', geometry: {} }],
  },
  selectedPatternZoneIds: ['zone-1'],
  speciesNames: new Map([['spirea', 'Спирея японская']]),
  onDetailed: vi.fn(),
});

describe('AutomaticPlacementPanel', () => {
  it('previews an automatic choice in the service without saving', () => {
    const state = props();
    render(<AutomaticPlacementPanel {...state as unknown as WorkspaceRecommendationToolProps} onDetailed={state.onDetailed} />);
    fireEvent.change(screen.getByLabelText('Ориентир для автопосадки'), {
      target: { value: 'building' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Найти места' }));
    expect(state.previewAutomatic.mutate).toHaveBeenCalledWith({
      base_plan_version: 7,
      zone_id: 'zone-1',
      near: 'building',
      plant_kind: 'auto',
      target_count: 8,
    });
    expect(state.applyChanges.mutate).not.toHaveBeenCalled();
  });

  it('requires explicit confirmation to apply the proposal', () => {
    const state = props();
    state.automaticPreview = {
      requested: 8, found: 8, shortfall: 0,
      selected_kind: 'shrub', selection_basis: 'Проверено',
      species_revision_ids: ['spirea'],
      change_set: { id: 'proposal', can_apply: true, additions: [] },
    } as unknown as AutomaticPlacementPreview;
    render(<AutomaticPlacementPanel {...state as unknown as WorkspaceRecommendationToolProps} onDetailed={state.onDetailed} />);
    expect(screen.getByText('Спирея японская')).toBeVisible();
    expect(state.applyChanges.mutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Добавить 8' }));
    expect(state.applyChanges.mutate).toHaveBeenCalledWith(state.automaticPreview.change_set);
  });
});
