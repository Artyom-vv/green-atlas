import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  WorkspaceZoneManagerDialog,
  type WorkspaceZoneManagerDialogProps,
} from './WorkspaceZoneManagerDialog';

afterEach(cleanup);

function props(): WorkspaceZoneManagerDialogProps {
  const zone = {
    id: 'west',
    label: 'Запад',
    geometry: {
      type: 'Polygon',
      coordinates: [
        [
          [0, 0],
          [10, 0],
          [10, 10],
          [0, 0],
        ],
      ],
    },
    territory: {
      category: 'courtyard' as const,
      regime: 'ordinary' as const,
      basis: 'Проект двора',
      spread_control_confirmed: false,
    },
  };
  return {
    project: {
      id: 'test',
      name: 'Тест участков',
      status: 'editing',
      map_ready: true,
      geometry_version: 1,
      state_version: 1,
      plan: { version: 1 },
      planting_zones: [zone],
    },
    panel: 'zones',
    zoneDrawingMode: undefined,
    zonePendingDelete: undefined,
    pendingZone: undefined,
    setPanel: vi.fn(),
    draftZones: [zone],
    selectedPatternZoneIds: ['west'],
    selectPatternZones: vi.fn(),
    plantingZoneUsage: {},
    saveManagedZones: {
      mutate: vi.fn(),
      reset: vi.fn(),
      isPending: false,
      isSuccess: false,
      error: undefined,
      submittedAt: 0,
    },
    focusZones: vi.fn(),
    setDraftZones: vi.fn(),
    sceneOpen: false,
    changeMapMode: vi.fn(),
    setSelectedPatternZoneIds: vi.fn(),
    beginZoneDrawing: vi.fn(),
    cancelZoneDrawing: vi.fn(),
    setZonePendingDelete: vi.fn(),
  };
}

describe('zone conditions navigation', () => {
  it('replaces the manager instead of stacking dialogs, and preserves its search on return', () => {
    render(<WorkspaceZoneManagerDialog {...props()} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Поиск участков' }), {
      target: { value: 'Запад' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Жилые территории' }));
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(
      screen.getByRole('dialog', { name: 'Условия участка: Запад' }),
    ).toBeVisible();
    expect(screen.getByLabelText('Основание выбора')).toHaveValue(
      'Проект двора',
    );
    fireEvent.click(screen.getByRole('button', { name: 'К участкам' }));
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(screen.getByRole('textbox', { name: 'Поиск участков' })).toHaveValue(
      'Запад',
    );
  });

  it('retains edits on failure and returns only after a confirmed synchronized save', async () => {
    const state = props();
    const view = render(<WorkspaceZoneManagerDialog {...state} />);
    fireEvent.click(screen.getByRole('button', { name: 'Жилые территории' }));
    fireEvent.change(screen.getByLabelText('Основание выбора'), {
      target: { value: 'Новый документ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить условия' }));
    await waitFor(() =>
      expect(state.saveManagedZones.mutate).toHaveBeenCalledOnce(),
    );
    expect(state.saveManagedZones.mutate).toHaveBeenCalledWith({
      focusId: 'west',
      zones: [
        {
          ...state.draftZones[0],
          territory: {
            ...state.draftZones[0].territory,
            basis: 'Новый документ',
          },
          site_conditions: null,
        },
      ],
    });
    view.rerender(
      <WorkspaceZoneManagerDialog
        {...state}
        saveManagedZones={{ ...state.saveManagedZones, isPending: true }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(screen.getByRole('button', { name: 'К участкам' })).toBeDisabled();
    expect(
      screen.getByRole('dialog', { name: 'Условия участка: Запад' }),
    ).toBeVisible();
    view.rerender(
      <WorkspaceZoneManagerDialog
        {...state}
        saveManagedZones={{
          ...state.saveManagedZones,
          error: new Error('Сеть недоступна'),
        }}
      />,
    );
    expect(
      within(
        screen.getByRole('dialog', { name: 'Условия участка: Запад' }),
      ).getByText('Сеть недоступна'),
    ).toBeVisible();
    expect(screen.getByLabelText('Основание выбора')).toHaveValue(
      'Новый документ',
    );
    view.rerender(
      <WorkspaceZoneManagerDialog
        {...state}
        saveManagedZones={{ ...state.saveManagedZones, isSuccess: true }}
      />,
    );
    expect(
      screen.getByRole('dialog', { name: 'Рабочие участки' }),
    ).toBeVisible();
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
  });
});
