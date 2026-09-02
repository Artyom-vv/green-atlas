import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WorkspaceNavigation } from './WorkspaceNavigation';

afterEach(cleanup);

describe('WorkspaceNavigation', () => {
  it('keeps the three primary destinations available and marks the current one', () => {
    render(<WorkspaceNavigation active="plantings" onChange={vi.fn()} issueCount={3} />);
    expect(screen.getByRole('navigation', { name: 'Разделы рабочего пространства' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Посадки' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: 'Участки' })).not.toHaveAttribute('aria-current');
    expect(screen.getByLabelText('3 замечаний')).toBeInTheDocument();
  });

  it('switches section in one click', () => {
    const onChange = vi.fn();
    render(<WorkspaceNavigation active="zones" onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Проверка' }));
    expect(onChange).toHaveBeenCalledWith('issues');
  });

  it('does not expose plan-only sections before a plan exists', () => {
    render(<WorkspaceNavigation active="zones" onChange={vi.fn()} hasPlan={false} />);
    expect(screen.getByRole('button', { name: 'Посадки' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Проверка' })).toBeDisabled();
  });
});
