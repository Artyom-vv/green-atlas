import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { Plus } from 'lucide-react';
import { IconButton } from '@green/ui';
import { MapControlGroup } from './MapControlGroup';

describe('MapControlGroup', () => {
  afterEach(cleanup);

  it('exposes the horizontal map control contract', () => {
    render(<MapControlGroup><IconButton icon={Plus} label="Увеличить" /></MapControlGroup>);
    expect(screen.getByRole('toolbar', { name: 'Управление картой' })).toHaveClass('editor-control-group--horizontal');
  });

  it('supports a vertical control stack without changing children', () => {
    render(<MapControlGroup orientation="vertical" label="Масштаб"><IconButton icon={Plus} label="Увеличить" /></MapControlGroup>);
    expect(screen.getByRole('toolbar', { name: 'Масштаб' })).toHaveClass('editor-control-group--vertical');
    expect(screen.getByRole('button', { name: 'Увеличить' })).toBeInTheDocument();
  });
});
