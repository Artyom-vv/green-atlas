import { MapViewSwitch } from '@/widgets/map/ui/MapViewSwitch';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

describe('MapViewSwitch', () => {
  it('keeps both view modes available and reports the next mode', () => {
    const onChange = vi.fn();
    render(<MapViewSwitch mode="2d" onChange={onChange} />);
    expect(screen.getByRole('button', { name: '2D' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    fireEvent.click(screen.getByRole('button', { name: '3D' }));
    expect(onChange).toHaveBeenCalledWith('3d');
  });
});
