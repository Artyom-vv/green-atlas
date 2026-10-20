import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type {
  AutomaticPlacementPreview,
  AutomaticPlacementRequest,
} from '@green/api-client';
import * as workspacePreviews from '@/features/workspace/api/workspacePreviews';
import { completeSearchDomainPreview } from '@/features/workspace/api/completeSearchDomainPreview';
import { useLatestPreview } from '@/shared/async/useLatestPreview';
import type { WorkspaceRecommendationToolProps } from './WorkspaceRecommendationTool.props';
import { AutomaticPlacementPanel } from './AutomaticPlacementPanel';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const props = () => ({
  applyChanges: { mutate: vi.fn(), isPending: false, error: undefined },
  automaticPreview: undefined as AutomaticPlacementPreview | undefined,
  previewAutomatic: {
    mutate: vi.fn(),
    reset: vi.fn(),
    cancel: vi.fn(),
    isPending: false,
    error: undefined,
  },
  project: {
    plan: { version: 7 },
    planting_zones: [{ id: 'zone-1', label: 'Участок 1', geometry: {} }],
  },
  selectedPatternZoneIds: ['zone-1'],
  speciesNames: new Map([['spirea', 'Спирея японская']]),
  onDetailed: vi.fn(),
});

function proposal(
  processedObjects: number,
  found = 0,
): AutomaticPlacementPreview {
  const completed = processedObjects === 10602;
  return {
    planning_horizon_year: 3,
    requested: 8,
    found,
    shortfall: 8 - found,
    selected_kind: 'tree',
    selection_basis: 'Проверено',
    species_revision_ids: [],
    search_domains: [
      {
        zone_id: 'zone-1',
        revision: 'prepared-geometry',
        method: 'hybrid',
        final_check: 'prepared_geometry',
        processed_objects: processedObjects,
        total_objects: 10602,
        cache_hits: 0,
        geometry: {},
        unresolved_geometry: {},
        available_area_m2: completed ? 100 : 0,
        excluded_area_m2: 0,
        unresolved_area_m2: 0,
        pending_area_m2: completed ? 0 : 100,
        minimum_cell_m: 0.5,
        measured_cells: 0,
        elapsed_s: 0,
        stop_reason: completed ? 'resolution' : 'time_limit',
      },
    ],
  };
}

function wireAutomaticPasses(
  next: (signal: AbortSignal) => Promise<AutomaticPlacementPreview>,
) {
  return vi
    .spyOn(workspacePreviews, 'previewWorkspaceAutomatic')
    .mockImplementation((_projectId, _request, signal, publish) =>
      completeSearchDomainPreview(next, signal, publish),
    );
}

// Подменяем транспорт через feature-границу. Продолжение и переходы состояния
// используют продуктовые функции; панель не обращается к API напрямую.
function LiveAutomaticPanel({ state }: { state: ReturnType<typeof props> }) {
  const previewAutomatic = useLatestPreview<
    AutomaticPlacementRequest,
    AutomaticPlacementPreview
  >({
    execute: (request, signal, publish) =>
      workspacePreviews.previewWorkspaceAutomatic(
        'fixture-project',
        request,
        signal,
        publish,
      ),
  });
  return (
    <AutomaticPlacementPanel
      {...(state as unknown as WorkspaceRecommendationToolProps)}
      automaticPreview={previewAutomatic.data}
      previewAutomatic={previewAutomatic}
      onDetailed={state.onDetailed}
    />
  );
}

describe('AutomaticPlacementPanel', () => {
  it('moves from an unfinished search to its result after one click, without showing a false zero', async () => {
    let finish!: (result: AutomaticPlacementPreview) => void;
    const request = vi
      .fn<(signal: AbortSignal) => Promise<AutomaticPlacementPreview>>()
      .mockResolvedValueOnce(proposal(2304))
      .mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve;
        }),
      );
    const start = wireAutomaticPasses(request);
    const state = props();
    render(<LiveAutomaticPanel state={state} />);

    fireEvent.click(screen.getByRole('button', { name: 'Найти места' }));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    expect(
      screen.getByRole('region', { name: 'Ход проверки области' }),
    ).toBeVisible();
    expect(screen.queryByText('Найдено 0 из 8')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Добавить/ }),
    ).not.toBeInTheDocument();
    expect(state.applyChanges.mutate).not.toHaveBeenCalled();

    await act(async () => finish(proposal(10602, 8)));

    expect(screen.getByText('Найдено 8 из 8')).toBeVisible();
    expect(
      screen.queryByRole('region', { name: 'Ход проверки области' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Отменить' }),
    ).not.toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(2);
    expect(start).toHaveBeenCalledOnce();
    expect(state.applyChanges.mutate).not.toHaveBeenCalled();
  });

  it('ends waiting on cancellation and ignores a late automatic reply', async () => {
    let finish!: (result: AutomaticPlacementPreview) => void;
    const request = vi
      .fn<(signal: AbortSignal) => Promise<AutomaticPlacementPreview>>()
      .mockResolvedValueOnce(proposal(2304))
      .mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve;
        }),
      );
    wireAutomaticPasses(request);
    render(<LiveAutomaticPanel state={props()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Найти места' }));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    const signal = request.mock.calls[1][0];
    fireEvent.click(screen.getByRole('button', { name: 'Отменить' }));

    expect(signal?.aborted).toBe(true);
    expect(screen.getByRole('button', { name: 'Найти места' })).toBeEnabled();
    expect(
      screen.queryByRole('region', { name: 'Ход проверки области' }),
    ).not.toBeInTheDocument();
    await act(async () => finish(proposal(10602, 8)));
    expect(screen.queryByText('Найдено 8 из 8')).not.toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it('ends waiting and exposes the error without repeating the failed pass', async () => {
    const request = vi
      .fn<(signal: AbortSignal) => Promise<AutomaticPlacementPreview>>()
      .mockResolvedValueOnce(proposal(2304))
      .mockRejectedValueOnce(new Error('Не удалось продолжить расчёт'));
    wireAutomaticPasses(request);
    render(<LiveAutomaticPanel state={props()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Найти места' }));

    expect(
      await screen.findByText('Не удалось продолжить расчёт'),
    ).toBeVisible();
    expect(screen.getByRole('button', { name: 'Найти места' })).toBeEnabled();
    expect(
      screen.queryByRole('region', { name: 'Ход проверки области' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('Найдено 0 из 8')).not.toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it('shows a completed search with zero positions without another request', async () => {
    const request = vi
      .fn<(signal: AbortSignal) => Promise<AutomaticPlacementPreview>>()
      .mockResolvedValueOnce(proposal(10602));
    wireAutomaticPasses(request);
    render(<LiveAutomaticPanel state={props()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Найти места' }));

    expect(await screen.findByText('Найдено 0 из 8')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Добавить 0' })).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Отменить' }),
    ).not.toBeInTheDocument();
    expect(request).toHaveBeenCalledOnce();
  });

  it('previews an automatic choice in the service without saving', () => {
    const state = props();
    render(
      <AutomaticPlacementPanel
        {...(state as unknown as WorkspaceRecommendationToolProps)}
        onDetailed={state.onDetailed}
      />,
    );
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
      requested: 8,
      found: 8,
      shortfall: 0,
      selected_kind: 'shrub',
      selection_basis: 'Проверено',
      species_revision_ids: ['spirea'],
      change_set: { id: 'proposal', can_apply: true, additions: [] },
    } as unknown as AutomaticPlacementPreview;
    render(
      <AutomaticPlacementPanel
        {...(state as unknown as WorkspaceRecommendationToolProps)}
        onDetailed={state.onDetailed}
      />,
    );
    expect(screen.getByText('Спирея японская')).toBeVisible();
    expect(state.applyChanges.mutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Добавить 8' }));
    expect(state.applyChanges.mutate).toHaveBeenCalledWith(
      state.automaticPreview.change_set,
    );
  });
});
