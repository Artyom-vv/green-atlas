import * as THREE from 'three';

/** Neutral illustrative finishes, in the same linear palette as Blender. */
const PALETTE: Record<string, [number, number, number]> = {
  building: [0.42, 0.425, 0.405],
  building_roof: [0.245, 0.255, 0.25],
  facade_plinth: [0.285, 0.295, 0.285],
  facade_glass: [0.095, 0.115, 0.126],
  facade_frame: [0.36, 0.37, 0.35],
  facade_joint: [0.2, 0.205, 0.195],
};

function mineralGrain() {
  const size = 64;
  const pixels = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      // Periodic low-contrast variation: no photographic stains or artificial
      // floor joints. UVs are already metric and shared by adjoining faces.
      const pore = ((x * 73856093) ^ (y * 19349663)) >>> 0;
      const value = Math.round(
        250 +
          2 * Math.sin((x / size) * Math.PI * 2) +
          2 * Math.cos((y / size) * Math.PI * 2) +
          (pore % 3) -
          1,
      );
      const offset = (y * size + x) * 4;
      pixels.set([value, value, value, 255], offset);
    }
  }
  const texture = new THREE.DataTexture(pixels, size, size, THREE.RGBAFormat);
  texture.name = 'Metric restrained mineral grain';
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.magFilter = THREE.LinearFilter;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.generateMipmaps = true;
  texture.needsUpdate = true;
  return texture;
}

export function facadeMaterial(kind: string) {
  const color = PALETTE[kind];
  if (!color) return undefined;
  const glass = kind === 'facade_glass';
  const frame = kind === 'facade_frame';
  const mineral =
    kind === 'building' || kind === 'building_roof' || kind === 'facade_plinth';
  const grain = mineral ? mineralGrain() : null;
  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color().setRGB(...color),
    roughness: glass ? 1 : frame ? 0.72 : 0.88,
    metalness: frame ? 0.03 : 0,
    // Windows are diffuse occupied openings, not invented reflected imagery.
    envMapIntensity: glass ? 0 : 1,
    map: grain,
    bumpMap: grain,
    bumpScale: grain ? 0.008 : 0,
  });
  material.name = `Prepared ${kind}`;
  return material;
}

/** Change only material ownership; the accepted cap and its holes stay exact. */
export function assignBuildingRoofGroups(geometry: THREE.BufferGeometry) {
  const normal = geometry.getAttribute('normal');
  const index = geometry.getIndex();
  const count = index?.count ?? normal.count;
  geometry.clearGroups();
  for (let start = 0; start < count; start += 3) {
    const first = index ? index.getX(start) : start;
    const materialIndex = normal.getY(first) > 0.99 ? 1 : 0;
    const previous = geometry.groups.at(-1);
    if (previous?.materialIndex === materialIndex) previous.count += 3;
    else geometry.addGroup(start, 3, materialIndex);
  }
}
