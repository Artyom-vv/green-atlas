import {
  MAP_DESIGN_CONTEXT_STROKE,
  MAP_DESIGN_PALETTE,
  MAP_DESIGN_TEXT_COLOR,
} from '../../../model/mapDesignPalette';
import type { CadAppearanceClass, CadLayerRoles } from './cadAppearanceTypes';

const CAD_CONTEXT_FILL_OPACITY = 0.12;
const CAD_RENDER_ORDER = { fill: -2, line: -1, text: 0 } as const;

export function cadDesignAppearance(
  layer: string,
  appearanceClass: CadAppearanceClass,
  roles: CadLayerRoles,
) {
  const palette = MAP_DESIGN_PALETTE[roles[layer]];
  const stroke = palette?.[1] ?? MAP_DESIGN_CONTEXT_STROKE;
  const isFill = appearanceClass === 'fill';
  const transparentFill = palette?.[0].startsWith('rgba');
  return {
    color:
      appearanceClass === 'text'
        ? MAP_DESIGN_TEXT_COLOR
        : isFill && palette
          ? palette[0]
          : stroke,
    opacity: isFill
      ? palette
        ? transparentFill
          ? 0
          : 1
        : CAD_CONTEXT_FILL_OPACITY
      : 1,
    renderOrder: CAD_RENDER_ORDER[appearanceClass],
  };
}
