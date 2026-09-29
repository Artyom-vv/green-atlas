import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { Layer } from '@green/api-client';
import { MapLegend } from './MapLegend';
import { MAP_DESIGN_PALETTE } from '../model/mapDesignPalette';
import { cadDesignAppearance } from '../lib/cad-renderer/appearance/cadDesignAppearance';

afterEach(cleanup);
it('uses distinct lawn/building/pavement fills and the same palette in the renderer', () => {
  expect(
    new Set(
      ['lawn', 'building', 'road'].map((kind) => MAP_DESIGN_PALETTE[kind][0]),
    ).size,
  ).toBe(3);
  expect(cadDesignAppearance('газон', 'fill', { газон: 'lawn' }).color).toBe(
    MAP_DESIGN_PALETTE.lawn[0],
  );
  expect(cadDesignAppearance('газон', 'line', { газон: 'lawn' }).color).toBe(
    MAP_DESIGN_PALETTE.lawn[1],
  );
  expect(MAP_DESIGN_PALETTE.utility[1]).not.toBe(
    MAP_DESIGN_PALETTE.restricted[1],
  );
  expect(cadDesignAppearance('сеть', 'line', { сеть: 'utility' }).color).toBe(
    MAP_DESIGN_PALETTE.utility[1],
  );
});
it('shows only mapped visible classes and never calls lawn a planting allowance', () => {
  render(
    <MapLegend
      layers={
        [
          { mapped_kind: 'lawn', visible: true, object_count: 1 },
          { mapped_kind: 'building', visible: false, object_count: 1 },
        ] as Layer[]
      }
    />,
  );
  expect(screen.getByText('Газоны')).toBeInTheDocument();
  expect(screen.queryByText('Здания')).not.toBeInTheDocument();
  expect(
    screen.getByText('Допустимые места показывает расчёт.'),
  ).toBeInTheDocument();
});
