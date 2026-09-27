import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  WorkspaceToolSlot,
  type WorkspaceToolSlotProps,
} from './WorkspaceToolSlot';

function EmptyPlacement() {
  return null;
}

const props: WorkspaceToolSlotProps = {
  tool: 'select',
  hasPlan: true,
  externalEditorBusy: false,
  placementAreaDrawing: false,
  pendingZone: undefined,
  placementCheck: undefined,
  activateTool: vi.fn(),
  setSingleShrubSpecies: vi.fn(),
  setSingleTreeSpecies: vi.fn(),
  singleShrubSpecies: '',
  singleTreeSpecies: '',
  singlePlacement: {
    checking: false,
    placing: false,
    error: undefined,
    notice: undefined,
    needsRefresh: false,
    refreshing: false,
    retryRefresh: vi.fn().mockResolvedValue(undefined),
  },
  speciesQuery: { data: [] },
  pattern: <div>Параметры ряда</div>,
  brush: <div>Параметры кисти</div>,
  placement: <EmptyPlacement />,
};

describe('workspace tool surface', () => {
  it('shows guidance when an inactive placement element renders nothing', () => {
    render(<WorkspaceToolSlot {...props} />);
    expect(
      screen.getByText(
        'Выберите инструмент на карте. Его параметры появятся здесь.',
      ),
    ).toBeVisible();
  });

  it('uses the placement slot only while placement owns the active task', () => {
    const placement = <div>Размещение на участке</div>;
    const { rerender } = render(
      <WorkspaceToolSlot
        {...props}
        tool="pattern_fill"
        placement={placement}
      />,
    );
    expect(screen.getByText('Размещение на участке')).toBeVisible();
    rerender(
      <WorkspaceToolSlot
        {...props}
        tool="draw_area"
        placementAreaDrawing
        placement={placement}
      />,
    );
    expect(screen.getByText('Размещение на участке')).toBeVisible();
    rerender(
      <WorkspaceToolSlot {...props} tool="pattern_row" placement={placement} />,
    );
    expect(screen.getByText('Параметры ряда')).toBeVisible();
    expect(screen.queryByText('Размещение на участке')).not.toBeInTheDocument();
  });
});
