import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ApiClientError } from '@green/api-client';
import { ProjectConflictNotice } from './ProjectConflictNotice';

describe('ProjectConflictNotice', () => {
  it('explains that the stale action was not applied and reloads explicitly', () => {
    const reload = vi.fn();
    render(<ProjectConflictNotice error={new ApiClientError('PROJECT_VERSION_CONFLICT', 'conflict')} onReload={reload} />);
    expect(screen.getByText('Проект обновлён в другой вкладке')).toBeInTheDocument();
    expect(screen.getByText(/Ваше действие не применено/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Обновить проект' }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it('stays absent for ordinary request errors', () => {
    const { container } = render(<ProjectConflictNotice error={new Error('offline')} onReload={() => undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
