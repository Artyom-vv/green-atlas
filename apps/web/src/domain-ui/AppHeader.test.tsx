import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { AppHeader } from './AppHeader';

describe('AppHeader workspace breadcrumbs', () => {
  it('keeps project hierarchy and the current workspace visible to assistive technology', () => {
    render(<MemoryRouter><AppHeader workspace projectName="ВДНХ" /></MemoryRouter>);

    const breadcrumbs = screen.getByRole('navigation', { name: 'Хлебные крошки' });
    expect(breadcrumbs).toHaveTextContent('Проекты');
    expect(breadcrumbs).toHaveTextContent('ВДНХ');
    expect(screen.getByText('ВДНХ')).toHaveAttribute('aria-current', 'page');
    expect(screen.getByText('План озеленения')).toBeVisible();
    expect(screen.getByRole('link', { name: 'К проектам' })).toHaveAttribute('href', '/projects');
  });
});
