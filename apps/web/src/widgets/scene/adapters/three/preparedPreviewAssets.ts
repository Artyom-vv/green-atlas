import * as THREE from 'three';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { PlantPrototype } from './plantAssets';

export type PreparedPreviewAsset = {
  id: string;
  sha256: string;
  url: string;
  size_bytes?: number;
  mime_type?: string;
  kind: 'model' | 'texture';
  lod?: 'near' | 'far' | null;
  bounds?: { min: number[]; max: number[] } | null;
};

export type PreparedPreviewMaterial = {
  kind: string;
  tile_width_m: number;
  normal_strength: number;
  maps: { diffuse?: string; roughness?: string; normal_gl?: string };
};

export interface PreparedPreviewAssetLoader {
  model(
    asset: PreparedPreviewAsset,
    signal: AbortSignal,
  ): Promise<PlantPrototype>;
  texture(
    asset: PreparedPreviewAsset,
    signal: AbortSignal,
  ): Promise<THREE.Texture>;
}

function abortError() {
  return new DOMException('Подготовка сцены отменена', 'AbortError');
}

function assertActive(signal: AbortSignal) {
  if (signal.aborted) throw abortError();
}

/** Resource URLs belong to the local API, including a separate Vite dev origin. */
export function preparedAssetUrl(path: string) {
  const base = import.meta.env?.VITE_API_URL ?? 'http://127.0.0.1:8000';
  const origin = new URL(base || window.location.origin).origin;
  const url = new URL(path, `${origin}/`);
  if (url.origin !== origin || !url.pathname.startsWith('/api/'))
    throw new Error('Ресурс оформления не принадлежит локальному API');
  return url.href;
}

async function verifiedBytes(asset: PreparedPreviewAsset, signal: AbortSignal) {
  assertActive(signal);
  const response = await fetch(preparedAssetUrl(asset.url), { signal });
  if (!response.ok)
    throw new Error(
      `Не удалось загрузить ресурс оформления (${response.status})`,
    );
  const bytes = await response.arrayBuffer();
  assertActive(signal);
  if (asset.size_bytes !== undefined && bytes.byteLength !== asset.size_bytes)
    throw new Error('Размер ресурса оформления не совпадает с подготовкой');
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const hash = [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('');
  assertActive(signal);
  if (hash !== asset.sha256)
    throw new Error('Ресурс оформления изменился после подготовки');
  return bytes;
}

export function assertEmbeddedPreparedGlb(bytes: ArrayBuffer) {
  const view = new DataView(bytes);
  if (
    bytes.byteLength < 20 ||
    view.getUint32(0, true) !== 0x46546c67 ||
    view.getUint32(4, true) !== 2 ||
    view.getUint32(8, true) !== bytes.byteLength ||
    view.getUint32(16, true) !== 0x4e4f534a
  )
    throw new Error('Ресурс растения не является подготовленной GLB-моделью');
  const length = view.getUint32(12, true);
  if (20 + length > bytes.byteLength)
    throw new Error('Повреждён каталог GLB-модели');
  const document = JSON.parse(
    new TextDecoder().decode(new Uint8Array(bytes, 20, length)),
  ) as {
    buffers?: Array<{ uri?: string }>;
    images?: Array<{ uri?: string }>;
  };
  if (
    [...(document.buffers ?? []), ...(document.images ?? [])].some(
      (row) => row.uri && !row.uri.startsWith('data:'),
    )
  )
    throw new Error(
      'GLB-модель содержит внешний ресурс вместо замороженной текстуры',
    );
}

function floatGeometry(source: THREE.BufferGeometry, matrix: THREE.Matrix4) {
  const geometry = source.clone();
  // Quantized GLB attributes must be dequantized before baking node transforms.
  for (const name of ['position', 'normal', 'tangent'] as const) {
    const attribute = geometry.getAttribute(name);
    if (!attribute) continue;
    const values = new Float32Array(attribute.count * attribute.itemSize);
    for (let index = 0; index < attribute.count; index++) {
      values[index * attribute.itemSize] = attribute.getX(index);
      if (attribute.itemSize > 1)
        values[index * attribute.itemSize + 1] = attribute.getY(index);
      if (attribute.itemSize > 2)
        values[index * attribute.itemSize + 2] = attribute.getZ(index);
      if (attribute.itemSize > 3)
        values[index * attribute.itemSize + 3] = attribute.getW(index);
    }
    geometry.setAttribute(
      name,
      new THREE.Float32BufferAttribute(values, attribute.itemSize),
    );
  }
  geometry.applyMatrix4(matrix);
  if (!geometry.getAttribute('normal')) geometry.computeVertexNormals();
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  return geometry;
}

export function preparedMaterialTextures(material: THREE.Material) {
  return Object.values(material).filter(
    (value): value is THREE.Texture => value instanceof THREE.Texture,
  );
}

export function disposePreparedTexture(texture: THREE.Texture) {
  texture.dispose();
  const image = texture.image as { close?: () => void } | undefined;
  image?.close?.();
}

export function disposePreparedPrototype(prototype: PlantPrototype) {
  const geometry = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();
  prototype.parts.forEach((part) => {
    geometry.add(part.geometry);
    for (const material of Array.isArray(part.material)
      ? part.material
      : [part.material]) {
      materials.add(material);
      preparedMaterialTextures(material).forEach((texture) =>
        textures.add(texture),
      );
    }
  });
  geometry.forEach((resource) => resource.dispose());
  materials.forEach((resource) => resource.dispose());
  textures.forEach(disposePreparedTexture);
}

export function extractPreparedPrototype(
  asset: PreparedPreviewAsset,
  root: THREE.Object3D,
): PlantPrototype {
  root.updateWorldMatrix(true, true);
  const parts: PlantPrototype['parts'] = [];
  const replacements = new Map<THREE.Material, THREE.Material>();
  const sourceGeometries = new Set<THREE.BufferGeometry>();
  const replacedMaterials = new Set<THREE.Material>();
  const replacedTextures = new Set<THREE.Texture>();
  const filteredTextures = new Set<THREE.Texture>();
  const runtimeMaterial = (source: THREE.Material) => {
    const previous = replacements.get(source);
    if (previous) return previous;
    const wholeTree = source.name.startsWith('whole-tree ');
    let material = source;
    if (wholeTree && source instanceof THREE.MeshStandardMaterial) {
      material = new THREE.MeshBasicMaterial({
        name: source.name,
        map: source.map,
        alphaMap: source.alphaMap,
        alphaTest: source.alphaTest,
      });
      replacedMaterials.add(source);
      preparedMaterialTextures(source).forEach((texture) =>
        replacedTextures.add(texture),
      );
    }
    if (material instanceof THREE.MeshBasicMaterial) {
      if (material.map) material.map.colorSpace = THREE.SRGBColorSpace;
      material.toneMapped = false;
      if (wholeTree) material.color.set(0xffffff);
    }
    if (
      material instanceof THREE.MeshStandardMaterial ||
      material instanceof THREE.MeshBasicMaterial
    ) {
      if (material.map) material.map.colorSpace = THREE.SRGBColorSpace;
      if (
        material.alphaMap ||
        material.alphaTest > 0 ||
        material.transparent ||
        wholeTree
      ) {
        material.transparent = false;
        // A whole-tree bake preserves the complete silhouette, including its
        // leaf density. Honor its frozen cutoff rather than treating a baked
        // silhouette as the separate fine leaf cards of the detailed model.
        material.alphaTest = wholeTree
          ? material.alphaTest > 0
            ? material.alphaTest
            : 0.35
          : Math.max(0.48, material.alphaTest);
        material.alphaToCoverage = false;
        material.depthWrite = true;
        material.side = THREE.DoubleSide;
      }
      if (material instanceof THREE.MeshStandardMaterial)
        material.metalness = 0;
    }
    // glTF samplers default to anisotropy 1. Leaf planes need filtering from
    // above as well as from the street; both mesh LODs keep the same sampling.
    for (const texture of preparedMaterialTextures(material)) {
      if (filteredTextures.has(texture)) continue;
      filteredTextures.add(texture);
      texture.minFilter = THREE.LinearMipmapLinearFilter;
      texture.magFilter = THREE.LinearFilter;
      texture.generateMipmaps = true;
      texture.anisotropy = 8;
      texture.needsUpdate = true;
    }
    replacements.set(source, material);
    return material;
  };
  root.traverse((object) => {
    if (
      !(object instanceof THREE.Mesh) ||
      !object.geometry.getAttribute('position')
    )
      return;
    const geometry = floatGeometry(object.geometry, object.matrixWorld);
    const sourceMaterials = Array.isArray(object.material)
      ? object.material
      : [object.material];
    const materials = sourceMaterials.map(runtimeMaterial);
    const overheadOnly = sourceMaterials.every((material) =>
      material.name.startsWith('whole-tree Botaniq multiview top'),
    )
      ? true
      : sourceMaterials.every((material) =>
            material.name.startsWith('whole-tree '),
          )
        ? false
        : undefined;
    parts.push({
      geometry,
      material: materials.length === 1 ? materials[0] : materials,
      castShadow:
        asset.lod !== 'far' &&
        materials.some(
          (material) => !(material instanceof THREE.MeshBasicMaterial),
        ),
      billboard: false,
      overheadOnly,
    });
    sourceGeometries.add(object.geometry);
  });
  sourceGeometries.forEach((geometry) => geometry.dispose());
  replacedMaterials.forEach((material) => material.dispose());
  const retainedTextures = new Set(
    parts.flatMap((part) =>
      (Array.isArray(part.material) ? part.material : [part.material]).flatMap(
        preparedMaterialTextures,
      ),
    ),
  );
  replacedTextures.forEach((texture) => {
    if (!retainedTextures.has(texture)) disposePreparedTexture(texture);
  });
  if (!parts.length) throw new Error('Модель растения не содержит геометрии');
  // Exporter has normalized both LODs to the native worker's same ground pivot,
  // height 1 and radius 1. Do not separately fit each LOD's narrower silhouette.
  return {
    assetKey: `prepared:${asset.id}`,
    lod: asset.lod ?? 'near',
    nominalHeightM: 1,
    nominalRadiusM: 1,
    parts,
  };
}

export const preparedPreviewAssetLoader: PreparedPreviewAssetLoader = {
  async model(asset, signal) {
    const bytes = await verifiedBytes(asset, signal);
    assertEmbeddedPreparedGlb(bytes);
    const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
    // GLB exports embed every texture. No arbitrary external URL is resolved.
    const gltf = await loader.parseAsync(bytes, '');
    const prototype = extractPreparedPrototype(asset, gltf.scene);
    if (signal.aborted) {
      disposePreparedPrototype(prototype);
      throw abortError();
    }
    return prototype;
  },
  async texture(asset, signal) {
    const bytes = await verifiedBytes(asset, signal);
    const blob = new Blob([bytes], { type: asset.mime_type ?? 'image/png' });
    const url = URL.createObjectURL(blob);
    let texture: THREE.Texture | undefined;
    try {
      texture = await new THREE.TextureLoader().loadAsync(url);
      if (signal.aborted) {
        disposePreparedTexture(texture);
        throw abortError();
      }
      texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      texture.minFilter = THREE.LinearMipmapLinearFilter;
      texture.magFilter = THREE.LinearFilter;
      texture.generateMipmaps = true;
      texture.anisotropy = 4;
      return texture;
    } finally {
      URL.revokeObjectURL(url);
    }
  },
};
