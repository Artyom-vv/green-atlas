import {
  MAP_DESIGN_CONTEXT_STROKE,
  MAP_DESIGN_PALETTE,
  MAP_DESIGN_TEXT_COLOR,
} from '../../../model/mapDesignPalette';
import type { CadAppearanceClass, CadLayerRoles } from './cadAppearanceTypes';

const CAD_FILL_OPACITY = 0.12;
const CAD_RENDER_ORDER = { fill: -2, line: -1, text: 0 } as const;

export function cadDesignAppearance(
  layer: string,
  appearanceClass: CadAppearanceClass,
  roles: CadLayerRoles,
) {
  const stroke =
    MAP_DESIGN_PALETTE[roles[layer]]?.[1] ?? MAP_DESIGN_CONTEXT_STROKE;
  return {
    color: appearanceClass === 'text' ? MAP_DESIGN_TEXT_COLOR : stroke,
    opacity: appearanceClass === 'fill' ? CAD_FILL_OPACITY : 1,
    renderOrder: CAD_RENDER_ORDER[appearanceClass],
  };
}
