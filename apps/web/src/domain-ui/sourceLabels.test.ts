import { describe, expect, it } from 'vitest';
import { isPrimarySingleBlockComponent, sourceBlockCaption, sourceLayerLabel } from './sourceLabels';

describe('sourceBlockCaption', () => {
  it('keeps a block name when it has no attributes', () => {
    expect(sourceBlockCaption('TREE_SYMBOL', undefined)).toBe('TREE_SYMBOL');
  });

  it('adds one useful source attribute without expanding the map label indefinitely', () => {
    expect(sourceBlockCaption('TREE_SYMBOL', { SPECIES: 'Липа мелколистная', AGE: '12' })).toBe('TREE_SYMBOL\nSPECIES: Липа мелколистная');
    expect(sourceBlockCaption('', { INVENTORY: 'A-17' })).toBe('INVENTORY: A-17');
  });

  it('labels only a single expanded block, not every member of an array', () => {
    expect(isPrimarySingleBlockComponent(1, 1)).toBe(true);
    expect(isPrimarySingleBlockComponent(2, 1)).toBe(false);
    expect(isPrimarySingleBlockComponent(1, 36)).toBe(false);
  });

  it('translates known source layers without hiding unknown CAD names', () => {
    expect(sourceLayerLabel('OSM_ROAD_LOCAL')).toBe('Местная дорога');
    expect(sourceLayerLabel('CUSTOM_AXIS')).toBe('CUSTOM_AXIS');
  });
});
