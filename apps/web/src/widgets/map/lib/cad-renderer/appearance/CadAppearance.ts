import { cadDesignAppearance } from './cadDesignAppearance';
import {
  isCadAppearanceObject,
  type CadAppearanceMaterial,
  type CadAppearanceMode,
  type CadLayerRoles,
} from './cadAppearanceTypes';

export class CadAppearance {
  private entries;
  private materials = new Map<
    CadAppearanceMaterial,
    Map<string, CadAppearanceMaterial>
  >();
  private mode: CadAppearanceMode = 'cad';
  private roleKey = '';

  constructor(children: readonly unknown[]) {
    this.entries = children.filter(isCadAppearanceObject).map((object) => ({
      object,
      originalMaterial: object.material,
      originalOrder: object.renderOrder,
    }));
  }

  apply(mode: CadAppearanceMode, roles: CadLayerRoles): boolean {
    const roleKey = JSON.stringify(
      Object.entries(roles).sort(([first], [second]) =>
        first.localeCompare(second),
      ),
    );
    if (this.mode === mode && (mode === 'cad' || this.roleKey === roleKey))
      return false;
    this.restore();
    this.mode = mode;
    this.roleKey = roleKey;
    if (mode === 'cad') return true;
    for (const { object, originalMaterial } of this.entries) {
      const { dxfLayer, dxfAppearanceClass } = object.userData;
      const appearance = cadDesignAppearance(
        dxfLayer,
        dxfAppearanceClass,
        roles,
      );
      const key = JSON.stringify(appearance);
      let variants = this.materials.get(originalMaterial);
      if (!variants) {
        variants = new Map();
        this.materials.set(originalMaterial, variants);
      }
      let material = variants.get(key);
      if (!material) {
        material = originalMaterial.clone();
        material.uniforms.color.value.set(appearance.color);
        material.uniforms.opacity.value = appearance.opacity;
        // One transparent queue preserves fill → line → opaque text order.
        material.transparent = true;
        variants.set(key, material);
      }
      object.material = material;
      object.renderOrder = appearance.renderOrder;
    }
    return true;
  }

  private restore() {
    for (const entry of this.entries) {
      entry.object.material = entry.originalMaterial;
      entry.object.renderOrder = entry.originalOrder;
    }
    for (const variants of this.materials.values()) {
      for (const material of variants.values()) material.dispose();
    }
    this.materials.clear();
  }

  dispose() {
    this.restore();
    this.entries = [];
  }
}
