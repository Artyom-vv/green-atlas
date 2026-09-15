import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  WorkspaceZoneReviewDialog,
  type WorkspaceZoneReviewDialogProps,
} from './WorkspaceZoneReviewDialog';

afterEach(cleanup);

function reviewProps() {
  return {
    pendingZone: {
      zone: {
        id: 'draft-east',
        label: 'Восточный участок',
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
      purpose: 'place',
      nextTool: 'pattern_row',
    },
    zoneReviewOpen: true,
    zoneReviewQuery: {
      data: { can_save: true, area_m2: 50, overlaps: [] },
      isFetching: false,
    },
    editorBusy: false,
    project: { planting_zones: [] },
    redrawZone: vi.fn(),
    cancelZoneReview: vi.fn(),
    setZoneReviewOpen: vi.fn(),
    saveManagedZones: { mutate: vi.fn(), isPending: false },
    savePlacementZone: { mutate: vi.fn(), isPending: false },
  } as unknown as WorkspaceZoneReviewDialogProps;
}

describe('zone review actions', () => {
  it('passes the original placement tool to the existing save command', () => {
    const props = reviewProps();
    render(<WorkspaceZoneReviewDialog {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить участок' }));
    expect(props.savePlacementZone.mutate).toHaveBeenCalledExactlyOnceWith({
      zone: props.pendingZone!.zone,
      nextTool: 'pattern_row',
    });
    expect(props.saveManagedZones.mutate).not.toHaveBeenCalled();
  });

  it('sends redraw and cancel through the drawing owner without saving', () => {
    const props = reviewProps();
    render(<WorkspaceZoneReviewDialog {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Перерисовать' }));
    expect(props.redrawZone).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Отменить' }));
    expect(props.cancelZoneReview).toHaveBeenCalledOnce();
    expect(props.savePlacementZone.mutate).not.toHaveBeenCalled();
    expect(props.saveManagedZones.mutate).not.toHaveBeenCalled();
  });

  it('keeps a submitted review owned by its pending write', () => {
    const props = reviewProps();
    props.editorBusy = true;
    props.savePlacementZone.isPending = true;
    render(<WorkspaceZoneReviewDialog {...props} />);
    expect(screen.getByRole('button', { name: 'Перерисовать' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Отменить' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Отменить' }));
    expect(props.cancelZoneReview).not.toHaveBeenCalled();
  });
});
