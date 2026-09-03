import type { ScenePlantObject } from '@green/api-client';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import type { SceneLod } from './sceneRenderContract';

export type PlantPrototypePart = {
  geometry: THREE.BufferGeometry;
  material: THREE.Material;
  castShadow: boolean;
};

export type PlantPrototype = {
  assetKey: string;
  lod: SceneLod;
  nominalHeightM: number;
  nominalRadiusM: number;
  parts: PlantPrototypePart[];
};

type PlantAssetManifest = {
  version: number;
  archetypes: Record<string, {
    nominalHeightM?: number;
    nominalRadiusM?: number;
    lods: Array<{ lod: SceneLod; url: string; triangleCount?: number }>;
  }>;
  speciesMap?: Record<string, string>;
};

type ScenePlantWithAsset = ScenePlantObject & { asset_key?: string | null; species_key?: string | null };

const FALLBACK_BY_SHAPE: Record<ScenePlantObject['crown_shape'], string> = {
  columnar: 'broadleaf-columnar',
  conical: 'conifer',
  irregular: 'broadleaf-irregular',
  oval: 'broadleaf-oval',
  placeholder: 'broadleaf-round',
  round: 'broadleaf-round',
  spreading: 'broadleaf-spreading',
};

function transformedGeometry(source: THREE.BufferGeometry, matrix: THREE.Matrix4) {
  const geometry = source.clone();
  geometry.applyMatrix4(matrix);
  geometry.computeVertexNormals();
  return geometry;
}

function materialForRuntime(source: THREE.Material) {
  const material = source.clone();
  if (material instanceof THREE.MeshStandardMaterial) {
    material.roughness = Math.max(0.58, material.roughness);
    material.metalness = 0;
    if (material.map) material.map.colorSpace = THREE.SRGBColorSpace;
    if (material.transparent || material.alphaMap) {
      material.transparent = false;
      material.alphaTest = Math.max(material.alphaTest, 0.42);
      material.depthWrite = true;
    }
  }
  return material;
}

function extractPrototype(assetKey: string, lod: SceneLod, root: THREE.Object3D, nominalHeightM?: number, nominalRadiusM?: number): PlantPrototype | undefined {
  root.updateWorldMatrix(true, true);
  const parts: PlantPrototypePart[] = [];
  const bounds = new THREE.Box3();
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh) || !object.geometry.attributes.position) return;
    const geometry = transformedGeometry(object.geometry, object.matrixWorld);
    geometry.computeBoundingBox();
    if (geometry.boundingBox) bounds.union(geometry.boundingBox);
    const sourceMaterials = Array.isArray(object.material) ? object.material : [object.material];
    if (sourceMaterials.length === 1) {
      parts.push({ geometry, material: materialForRuntime(sourceMaterials[0]), castShadow: true });
      return;
    }
    // Multi-material meshes are kept as one primitive. Instancing preserves
    // their groups without multiplying one plant into many React objects.
    parts.push({ geometry, material: sourceMaterials.map(materialForRuntime) as unknown as THREE.Material, castShadow: true });
  });
  if (!parts.length || bounds.isEmpty()) return undefined;
  const size = bounds.getSize(new THREE.Vector3());
  return {
    assetKey,
    lod,
    nominalHeightM: nominalHeightM && nominalHeightM > 0 ? nominalHeightM : Math.max(0.01, size.y),
    nominalRadiusM: nominalRadiusM && nominalRadiusM > 0 ? nominalRadiusM : Math.max(0.01, Math.max(size.x, size.z) / 2),
    parts,
  };
}

function merge(parts: THREE.BufferGeometry[]) {
  const geometry = mergeGeometries(parts, false);
  parts.forEach((part) => part.dispose());
  if (!geometry) throw new Error('Не удалось собрать резервную модель растения');
  geometry.computeVertexNormals();
  return geometry;
}

function branch(radius: number, length: number, origin: THREE.Vector3, direction: THREE.Vector3, radialSegments: number) {
  const geometry = new THREE.CylinderGeometry(radius * 0.72, radius, length, radialSegments, 1, false);
  const up = new THREE.Vector3(0, 1, 0);
  const normalized = direction.clone().normalize();
  const quaternion = new THREE.Quaternion().setFromUnitVectors(up, normalized);
  const center = origin.clone().addScaledVector(normalized, length / 2);
  geometry.applyQuaternion(quaternion);
  geometry.translate(center.x, center.y, center.z);
  return geometry;
}

function fallbackPrototype(assetKey: string, lod: SceneLod): PlantPrototype {
  const shrub = assetKey.startsWith('shrub');
  const conifer = assetKey === 'conifer';
  const spreading = assetKey.endsWith('spreading');
  const columnar = assetKey.endsWith('columnar');
  const irregular = assetKey.endsWith('irregular');
  const detail = lod === 'near' ? 10 : lod === 'mid' ? 7 : 5;
  const nominalHeightM = shrub ? 1.4 : conifer ? 10 : 8;
  const nominalRadiusM = shrub ? 1 : spreading ? 3.8 : columnar ? 1.8 : 3;
  const bark = new THREE.MeshStandardMaterial({ color: shrub ? 0x6a7354 : 0x66503b, roughness: 0.96, metalness: 0 });
  const foliage = new THREE.MeshStandardMaterial({
    color: conifer ? 0x1f6045 : shrub ? 0x557a50 : 0x357452,
    roughness: 0.9,
    metalness: 0,
    flatShading: lod === 'far',
  });

  if (shrub) {
    const clusters = lod === 'near' ? 7 : lod === 'mid' ? 3 : 1;
    const geometries = Array.from({ length: clusters }, (_, index) => {
      const angle = (index / Math.max(1, clusters - 1)) * Math.PI * 2;
      const radius = clusters === 1 ? 0 : 0.38;
      const geometry = new THREE.IcosahedronGeometry(clusters === 1 ? 1 : 0.62, lod === 'near' ? 2 : 1);
      geometry.scale(1, 0.68, 1);
      geometry.translate(Math.cos(angle) * radius, 0.6 + (index % 2) * 0.14, Math.sin(angle) * radius);
      return geometry;
    });
    return { assetKey, lod, nominalHeightM, nominalRadiusM, parts: [{ geometry: merge(geometries), material: foliage, castShadow: lod !== 'far' }] };
  }

  const trunkParts = [new THREE.CylinderGeometry(0.16, 0.3, conifer ? 5.4 : 4, detail, 2, false)];
  trunkParts[0].translate(0, conifer ? 2.7 : 2, 0);
  if (lod !== 'far') {
    const branchCount = lod === 'near' ? 8 : 4;
    for (let index = 0; index < branchCount; index += 1) {
      const angle = index * 2.39996;
      const origin = new THREE.Vector3(0, 2.9 + (index % 3) * 0.42, 0);
      const length = conifer ? 1.6 + (index % 2) * 0.35 : 1.2 + (index % 3) * 0.28;
      trunkParts.push(branch(0.08, length, origin, new THREE.Vector3(Math.cos(angle), conifer ? 0.06 : 0.35, Math.sin(angle)), detail));
    }
  }

  const foliageParts: THREE.BufferGeometry[] = [];
  if (conifer) {
    const tiers = lod === 'near' ? 6 : lod === 'mid' ? 3 : 1;
    for (let index = 0; index < tiers; index += 1) {
      const geometry = new THREE.ConeGeometry(2.35 - index * 0.24, 3.25, detail * 2, 2);
      geometry.translate(0, 4.8 + index * 0.72, 0);
      foliageParts.push(geometry);
    }
  } else {
    const clusterCount = lod === 'near' ? 9 : lod === 'mid' ? 4 : 1;
    for (let index = 0; index < clusterCount; index += 1) {
      const angle = index * 2.39996;
      const radial = clusterCount === 1 ? 0 : (spreading ? 1.55 : columnar ? 0.62 : 1.05) * (0.55 + (index % 3) * 0.2);
      const geometry = new THREE.IcosahedronGeometry(clusterCount === 1 ? 2.5 : 1.25 + (index % 2) * 0.18, lod === 'near' ? 2 : 1);
      geometry.scale(spreading ? 1.35 : columnar ? 0.72 : 1, columnar ? 1.42 : spreading ? 0.72 : 1, irregular && index % 2 ? 0.72 : 1);
      geometry.translate(Math.cos(angle) * radial, 5.2 + (index % 3) * 0.62, Math.sin(angle) * radial);
      foliageParts.push(geometry);
    }
  }
  return {
    assetKey,
    lod,
    nominalHeightM,
    nominalRadiusM,
    parts: [
      { geometry: merge(trunkParts), material: bark, castShadow: lod !== 'far' },
      { geometry: merge(foliageParts), material: foliage, castShadow: lod !== 'far' },
    ],
  };
}

export class PlantAssetLibrary {
  private readonly prototypes = new Map<string, PlantPrototype>();
  private readonly speciesMap = new Map<string, string>();
  private readonly fallbackKeys = new Set<string>();

  static async load(manifestUrl = '/assets/plant-models/manifest.json') {
    const library = new PlantAssetLibrary();
    try {
      const response = await fetch(manifestUrl, { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(`manifest ${response.status}`);
      const manifest = await response.json() as PlantAssetManifest;
      Object.entries(manifest.speciesMap ?? {}).forEach(([species, archetype]) => library.speciesMap.set(species, archetype));
      const loader = new GLTFLoader();
      await Promise.all(Object.entries(manifest.archetypes).flatMap(([assetKey, entry]) => entry.lods.map(async (lod) => {
        try {
          const gltf = await loader.loadAsync(lod.url);
          const prototype = extractPrototype(assetKey, lod.lod, gltf.scene, entry.nominalHeightM, entry.nominalRadiusM);
          if (prototype) library.prototypes.set(`${assetKey}:${lod.lod}`, prototype);
        } catch {
          // A missing optional model should degrade to the documented local
          // archetype, never make the entire project scene unavailable.
        }
      })));
    } catch {
      // The fallback library is intentionally usable offline.
    }
    return library;
  }

  assetKey(object: ScenePlantWithAsset) {
    const explicit = object.model_variant_key?.trim() || object.asset_key?.trim() || object.species_key?.trim();
    if (explicit && this.speciesMap.has(explicit)) return this.speciesMap.get(explicit)!;
    const scientificName = object.scientific_name?.trim();
    if (scientificName && this.speciesMap.has(scientificName)) return this.speciesMap.get(scientificName)!;
    const speciesId = object.species_id?.trim();
    if (speciesId && this.speciesMap.has(speciesId)) return this.speciesMap.get(speciesId)!;
    if (explicit && this.prototypes.has(`${explicit}:near`)) return explicit;
    const revision = object.species_revision_id?.trim();
    if (revision) return this.speciesMap.get(revision) ?? revision;
    if (object.kind === 'shrub') return object.crown_shape === 'spreading' ? 'shrub-spreading' : 'shrub-round';
    return FALLBACK_BY_SHAPE[object.crown_shape];
  }

  get(object: ScenePlantWithAsset, lod: SceneLod) {
    let assetKey = this.assetKey(object);
    let prototype = this.prototypes.get(`${assetKey}:${lod}`);
    if (!prototype && object.species_revision_id) {
      assetKey = object.kind === 'shrub'
        ? object.crown_shape === 'spreading' ? 'shrub-spreading' : 'shrub-round'
        : FALLBACK_BY_SHAPE[object.crown_shape];
      prototype = this.prototypes.get(`${assetKey}:${lod}`);
    }
    if (!prototype) {
      const key = `${assetKey}:${lod}`;
      prototype = fallbackPrototype(assetKey, lod);
      this.prototypes.set(key, prototype);
      this.fallbackKeys.add(key);
    }
    return prototype;
  }

  loadedModelCount() {
    return this.prototypes.size - this.fallbackKeys.size;
  }

  dispose() {
    for (const prototype of this.prototypes.values()) {
      for (const part of prototype.parts) {
        part.geometry.dispose();
        if (Array.isArray(part.material)) part.material.forEach((material) => material.dispose());
        else part.material.dispose();
      }
    }
    this.prototypes.clear();
    this.fallbackKeys.clear();
  }
}
