import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { WorkspaceSourceInspector } from './WorkspaceSourceInspector';

describe('CAD preview source inspector', () => {
  it('explains the source limitation once and opens its data instead of requesting a release ZIP', () => {
    const message =
      'Предварительная карта. Полнота комплекта ещё не подтверждена.';
    const navigate = vi.fn();
    render(
      <WorkspaceSourceInspector
        inspectorView="source"
        planLocked
        sourceWarnings={[message, 'Внешняя ссылка не найдена']}
        sourceImportStatus={{
          mode: 'cad_preview',
          editability: 'read_only',
          message,
        }}
        openLeftPanel={vi.fn()}
        navigate={navigate}
        projectId="preview"
      />,
    );
    expect(screen.getAllByText(message)).toHaveLength(1);
    expect(
      screen.queryByRole('button', { name: 'Загрузить полный ZIP' }),
    ).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Исходные данные' }));
    expect(navigate).toHaveBeenCalledWith('/projects/preview/setup');
  });
});
