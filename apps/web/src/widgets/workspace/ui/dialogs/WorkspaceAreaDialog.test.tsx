import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  WorkspaceAreaDialog,
  type WorkspaceAreaDialogProps,
} from './WorkspaceAreaDialog';

afterEach(cleanup);

function areaProps(): WorkspaceAreaDialogProps {
  return {
    inspectorView: 'area',
    mapAreaTarget: {
      sourceId: 'source-polygon',
      kind: 'allowed',
      label: 'Допустимая область',
      detail: 'Можно включить в рабочую зону',
      selectable: true,
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
    },
    project: { plan: {}, planting_zones: [] },
    planLocked: false,
    editorBusy: false,
    setMapAreaTarget: vi.fn(),
    activateTool: vi.fn(),
    setSelectedPatternZoneIds: vi.fn(),
    setPendingZone: vi.fn(),
    setZoneReviewOpen: vi.fn(),
  } as unknown as WorkspaceAreaDialogProps;
}

describe('area inspection dialog', () => {
  it('shows the title once and puts the working-zone command in the footer', () => {
    const props = areaProps();
    render(<WorkspaceAreaDialog {...props} />);
    expect(screen.getAllByText('Допустимая область')).toHaveLength(1);
    const action = screen.getByRole('button', {
      name: 'Сделать рабочим участком',
    });
    expect(action.closest('[data-slot="dialog-footer"]')).not.toBeNull();
    expect(action.closest('[data-slot="dialog-body"]')).toBeNull();
    fireEvent.click(action);
    expect(props.setPendingZone).toHaveBeenCalledExactlyOnceWith({
      zone: {
        id: 'source-polygon',
        label: 'Допустимая область',
        geometry: props.mapAreaTarget?.geometry,
      },
      purpose: 'place',
    });
    expect(props.setZoneReviewOpen).toHaveBeenCalledWith(true);
    expect(props.setMapAreaTarget).toHaveBeenCalledWith(undefined);
  });

  it('returns an existing zone to the existing placement command', () => {
    const props = areaProps();
    props.mapAreaTarget!.plantingZoneId = 'zone-east';
    render(<WorkspaceAreaDialog {...props} />);
    expect(screen.getByText('Участок проекта')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Разместить здесь' }));
    expect(props.activateTool).toHaveBeenCalledWith('pattern_fill');
    expect(props.setSelectedPatternZoneIds).toHaveBeenCalledWith(['zone-east']);
    expect(props.setPendingZone).not.toHaveBeenCalled();
  });

  it('leaves read-only information without an empty action strip', () => {
    const props = areaProps();
    props.mapAreaTarget!.selectable = false;
    render(<WorkspaceAreaDialog {...props} />);
    expect(
      screen.getByRole('dialog').querySelector('[data-slot="dialog-footer"]'),
    ).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    expect(props.setMapAreaTarget).toHaveBeenCalledWith(undefined);
  });

  it('keeps mutation actions disabled while the editor is busy', () => {
    const props = areaProps();
    props.editorBusy = true;
    render(<WorkspaceAreaDialog {...props} />);
    fireEvent.click(
      screen.getByRole('button', { name: 'Сделать рабочим участком' }),
    );
    expect(props.setPendingZone).not.toHaveBeenCalled();
  });
});
