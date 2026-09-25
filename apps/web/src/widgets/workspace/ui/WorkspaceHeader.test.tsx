import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WorkspaceHeader, type WorkspaceHeaderProps } from './WorkspaceHeader';

afterEach(cleanup);

function props(overrides: Partial<WorkspaceHeaderProps> = {}): WorkspaceHeaderProps {
  return {
    project: { id: 'street', name: 'Улица', source_file: { accept_partial_geometry: true } },
    historyQuery: { data: { can_undo: false, can_redo: false } },
    undoChange: { mutate: vi.fn() },
    redoChange: { mutate: vi.fn() },
    createRelease: { isPending: false },
    leaveWorkspace: vi.fn(),
    setReleaseOpen: vi.fn(),
    projectHasPlan: true,
    editorBusy: false,
    savingPlan: false,
    changePreview: null,
    ...overrides,
  } as WorkspaceHeaderProps;
}

describe('Workspace source notice in header', () => {
  it('puts one compact notice inside the header and preserves source navigation', () => {
    const input = props();
    render(<WorkspaceHeader {...input} />);
    const label = 'Исходные данные: есть замечания';
    const notice = within(screen.getByRole('banner')).getByRole('button', { name: label });
    expect(screen.getAllByRole('button', { name: label })).toHaveLength(1);
    expect(notice).toHaveTextContent('Замечания');
    fireEvent.click(notice);
    expect(input.leaveWorkspace).toHaveBeenCalledWith('/projects/street/setup');
  });

  it('does not show a warning for a project without source issues', () => {
    const input = props();
    input.project.source_file = null;
    render(<WorkspaceHeader {...input} />);
    expect(screen.queryByRole('button', { name: /замечания/i })).not.toBeInTheDocument();
  });

  it('keeps a display warning available without adding a map banner', () => {
    render(<WorkspaceHeader {...props({ cadDisplayWarning: 'Часть объектов не показана' })} />);
    const notice = within(screen.getByRole('banner')).getByRole('button', { name: 'Показана доступная геометрия' });
    expect(notice).toHaveAttribute('title', 'Часть объектов не показана');
  });
});
