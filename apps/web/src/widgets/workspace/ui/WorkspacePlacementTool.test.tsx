import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import {
  WorkspacePlacementTool,
  type WorkspacePlacementToolProps,
} from './WorkspacePlacementTool';

afterEach(cleanup);

it('opens the manual form directly and keeps it mounted while drawing and reviewing a zone', () => {
  const props = {
    busy: false,
    closeRightPanel: vi.fn(),
    patternSettingsOpen: true,
    placementAreaDrawing: false,
    recommendationOpen: false,
    setRecommendationOpen: vi.fn(),
    patternTool: <input aria-label="Черновик размещения" defaultValue="24" />,
  } as unknown as WorkspacePlacementToolProps;
  const { rerender } = render(<WorkspacePlacementTool {...props} />);
  const draft = screen.getByRole('textbox', { name: 'Черновик размещения' });
  expect(screen.queryByRole('button', { name: 'Подобрать автоматически' })).toBeNull();
  fireEvent.change(draft, { target: { value: '32' } });

  rerender(
    <WorkspacePlacementTool
      {...props}
      patternSettingsOpen={false}
      placementAreaDrawing
    />,
  );
  expect(draft).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Способ подбора' })).toBeNull();

  rerender(
    <WorkspacePlacementTool
      {...props}
      patternSettingsOpen={false}
      pendingZone={{
        zone: { id: 'draft', label: 'Новый участок', geometry: {} },
        purpose: 'place',
      }}
    />,
  );
  expect(draft).toBeVisible();
  rerender(<WorkspacePlacementTool {...props} />);
  expect(screen.getByRole('textbox', { name: 'Черновик размещения' })).toBe(
    draft,
  );
  expect(draft).toHaveValue('32');
});
