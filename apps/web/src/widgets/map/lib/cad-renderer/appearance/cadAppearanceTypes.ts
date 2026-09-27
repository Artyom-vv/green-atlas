export type {
  CadAppearanceMode,
  CadLayerRoles,
} from '../../../model/cadSource';
export type CadAppearanceClass = 'line' | 'fill' | 'text';

export interface CadAppearanceMaterial {
  uniforms: {
    color: { value: { set: (color: string) => unknown } };
    opacity: { value: number };
  };
  transparent: boolean;
  clone: () => CadAppearanceMaterial;
  dispose: () => void;
}

export interface CadAppearanceObject {
  material: CadAppearanceMaterial;
  renderOrder: number;
  userData: { dxfLayer: string; dxfAppearanceClass: CadAppearanceClass };
}

/** Only our pinned SDK's public metadata/uniform contract may be restyled. */
export function isCadAppearanceObject(
  value: unknown,
): value is CadAppearanceObject {
  if (!value || typeof value !== 'object') return false;
  const object = value as Partial<CadAppearanceObject>;
  return (
    typeof object.userData?.dxfLayer === 'string' &&
    ['line', 'fill', 'text'].includes(object.userData.dxfAppearanceClass) &&
    typeof object.material?.clone === 'function' &&
    typeof object.material.uniforms?.color?.value?.set === 'function' &&
    typeof object.material.uniforms?.opacity?.value === 'number'
  );
}
