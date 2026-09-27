import { MapControlGroup } from '@/widgets/map/ui/MapControlGroup';
import { IconButton } from '@green/ui';
import { cleanup, render, screen } from '@testing-library/react';
import { Plus } from 'lucide-react';
import { afterEach, describe, expect, it } from 'vitest';

describe('MapControlGroup', () => {
  afterEach(cleanup);

  it('exposes the horizontal map control contract', () => {
    render(
      <MapControlGroup>
        <IconButton icon={Plus} label="Увеличить" />
      </MapControlGroup>,
    );
    expect(
      screen.getByRole('toolbar', { name: 'Управление картой' }),
    ).toHaveAttribute('aria-orientation', 'horizontal');
  });

  it('supports a vertical control stack without changing children', () => {
    render(
      <MapControlGroup orientation="vertical" label="Масштаб">
        <IconButton icon={Plus} label="Увеличить" />
      </MapControlGroup>,
    );
    expect(screen.getByRole('toolbar', { name: 'Масштаб' })).toHaveAttribute(
      'aria-orientation',
      'vertical',
    );
    expect(
      screen.getByRole('button', { name: 'Увеличить' }),
    ).toBeInTheDocument();
  });
});
