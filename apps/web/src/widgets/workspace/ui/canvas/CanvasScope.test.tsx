import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { CanvasScope, type CanvasScopeProps } from './CanvasScope';

afterEach(cleanup);

it('keeps the idle tool hint but leaves operation feedback to its owner', () => {
  const props = {
    activeToolHint: 'Участков для расчёта: 1',
    editorBusy: false,
    ideRightTab: 'tool',
    rightOpen: false,
    sceneOpen: false,
    tool: 'pattern_fill',
    toolNames: { pattern_fill: 'Разместить посадки' },
  } as CanvasScopeProps;
  const { rerender } = render(<CanvasScope {...props} />);
  expect(screen.getByRole('status')).toHaveTextContent(
    'Участков для расчёта: 1',
  );
  rerender(<CanvasScope {...props} editorBusy />);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  rerender(<CanvasScope {...props} />);
  expect(screen.getByRole('status')).toHaveTextContent('Разместить посадки');
});
