import type { ScenePlantObject } from '@green/api-client';
import * as THREE from 'three';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { SceneLod } from '@/widgets/scene/model/sceneRenderContract';

export type PlantPrototypePart = {
  geometry: THREE.BufferGeometry;
  material: THREE.Material | THREE.Material[];
  castShadow: boolean;
  billboard: boolean;
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
  archetypes: Record<
    string,
    {
      nominalHeightM?: number;
      nominalRadiusM?: number;
      lods: Array<{
        lod: SceneLod;
        url: string;
        ktx2Url?: string;
        triangleCount?: number;
        foliageRepresentation?: string;
      }>;
    }
  >;
  speciesMap?: Record<string, string>;
};

export type PlantAssetLoadedEvent = {
  assetKey: string;
  lod: SceneLod;
  format: 'glb';
};

export type PlantAssetLibraryLoadOptions = {
  renderer?: THREE.WebGLRenderer;
  preferKtx2?: boolean;
  ktx2TranscoderPath?: string;
  onPrototypeLoaded?: (event: PlantAssetLoadedEvent) => void;
};

export type PlantAssetLibraryLease = {
  library: Promise<PlantAssetLibrary>;
  release: () => void;
};

type ScenePlantWithAsset = ScenePlantObject & {
  asset_key?: string | null;
  species_key?: string | null;
};

const FALLBACK_BY_SHAPE: Record<ScenePlantObject['crown_shape'], string> = {
  columnar: 'broadleaf-oval',
  conical: 'fir-natural',
  irregular: 'broadleaf-spreading',
  oval: 'broadleaf-oval',
  placeholder: 'broadleaf-round',
  round: 'broadleaf-round',
  spreading: 'broadleaf-spreading',
};

function transformedGeometry(
  source: THREE.BufferGeometry,
  matrix: THREE.Matrix4,
) {
  const geometry = source.clone();
  // Meshopt/KHR_mesh_quantization stores positions as normalized integers and
  // keeps the real metre scale on the glTF node. BufferGeometry.applyMatrix4
  // writes back through the integer attribute, clipping transformed values to
  // [-1, 1] and turning every plant into a small cube. Dequantize the spatial
  // attributes before baking the node transform into reusable instanced data.
  for (const name of ['position', 'normal', 'tangent'] as const) {
    const attribute = geometry.getAttribute(name);
    if (!attribute) continue;
    const values = new Float32Array(attribute.count * attribute.itemSize);
    for (let index = 0; index < attribute.count; index += 1) {
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
  if (!geometry.attributes.normal) geometry.computeVertexNormals();
  return geometry;
}

function materialForRuntime(source: THREE.Material) {
  if (source.name.startsWith('whole-tree ')) {
    const standard = source as THREE.MeshStandardMaterial;
    if (standard.map) standard.map.colorSpace = THREE.SRGBColorSpace;
    return new THREE.MeshBasicMaterial({
      name: source.name,
      map: standard.map ?? null,
      // The whole-tree texture is already a colour-managed Blender bake.
      // Re-applying the foliage PBR tint or ACES tone mapping here crushed
      // conifer greens to almost black in the actual workspace.
      color: 0xffffff,
      alphaTest: Math.max(0.48, standard.alphaTest),
      // Alpha-to-coverage turns every sub-pixel leaf edge into a bright,
      // temporally unstable fringe once hundreds of cutout cards overlap.
      // A stable mask is deliberately preferable for the planning viewport.
      alphaToCoverage: false,
      depthWrite: true,
      side: THREE.DoubleSide,
      transparent: false,
      toneMapped: false,
    });
  }
  const material = source.clone();
  if (material instanceof THREE.MeshStandardMaterial) {
    material.roughness = Math.max(0.58, material.roughness);
    material.metalness = 0;
    if (material.map) material.map.colorSpace = THREE.SRGBColorSpace;
    if (material.transparent || material.alphaMap || material.alphaTest > 0) {
      material.transparent = false;
      // Keep partially transparent mip pixels out of the framebuffer. With
      // alpha-to-coverage these pixels picked up the pale background and drew
      // a white contour around every leaf card while the camera was moving.
      material.alphaTest = Math.max(0.48, material.alphaTest);
      material.alphaToCoverage = false;
      material.depthWrite = true;
      material.side = THREE.DoubleSide;
    }
  }
  return material;
}

function extractPrototype(
  assetKey: string,
  lod: SceneLod,
  root: THREE.Object3D,
  nominalHeightM?: number,
  nominalRadiusM?: number,
): PlantPrototype | undefined {
  root.updateWorldMatrix(true, true);
  const parts: PlantPrototypePart[] = [];
  const bounds = new THREE.Box3();
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh) || !object.geometry.attributes.position)
      return;
    const geometry = transformedGeometry(object.geometry, object.matrixWorld);
    geometry.computeBoundingBox();
    if (geometry.boundingBox) bounds.union(geometry.boundingBox);
    const sourceMaterials = Array.isArray(object.material)
      ? object.material
      : [object.material];
    const billboard =
      lod !== 'near' &&
      sourceMaterials.some(
        (material) =>
          material.name.startsWith('whole-tree ') ||
          material.name.startsWith('source-derived foliage impostor'),
      );
    parts.push({
      geometry,
      material:
        sourceMaterials.length === 1
          ? materialForRuntime(sourceMaterials[0])
          : sourceMaterials.map((material) => materialForRuntime(material)),
      castShadow:
        lod !== 'far' &&
        !sourceMaterials.some((material) =>
          material.name.startsWith('whole-tree '),
        ),
      billboard,
    });
  });
  if (!parts.length || bounds.isEmpty()) return undefined;
  const size = bounds.getSize(new THREE.Vector3());
  return {
    assetKey,
    lod,
    nominalHeightM:
      nominalHeightM && nominalHeightM > 0
        ? nominalHeightM
        : Math.max(0.01, size.y),
    nominalRadiusM:
      nominalRadiusM && nominalRadiusM > 0
        ? nominalRadiusM
        : Math.max(0.01, Math.max(size.x, size.z) / 2),
    parts,
  };
}

function defaultAssetKey(object: ScenePlantWithAsset) {
  if (object.kind === 'shrub') return 'shrub-natural';
  if (object.crown_shape === 'conical') return 'fir-natural';
  return FALLBACK_BY_SHAPE[object.crown_shape];
}

function materialTextures(material: THREE.Material) {
  const textures: THREE.Texture[] = [];
  for (const value of Object.values(material)) {
    if (value instanceof THREE.Texture) textures.push(value);
  }
  return textures;
}

export class PlantAssetLibrary {
  private readonly prototypes = new Map<string, PlantPrototype>();
  private readonly speciesMap = new Map<string, string>();
  private readonly failedUrls = new Set<string>();
  private readonly listeners = new Set<
    (event: PlantAssetLoadedEvent) => void
  >();
  private upgradesPromise: Promise<void> = Promise.resolve();
  private upgradeTimer?: ReturnType<typeof setTimeout>;
  private resolveUpgrades?: () => void;
  private disposed = false;

  static async load(
    manifestUrl = '/assets/plant-models/manifest.json',
    options: PlantAssetLibraryLoadOptions = {},
  ) {
    const library = new PlantAssetLibrary();
    if (options.onPrototypeLoaded)
      library.listeners.add(options.onPrototypeLoaded);
    try {
      const response = await fetch(manifestUrl, {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`manifest ${response.status}`);
      const manifest = (await response.json()) as PlantAssetManifest;
      Object.entries(manifest.speciesMap ?? {}).forEach(
        ([species, archetype]) => library.speciesMap.set(species, archetype),
      );
      const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
      const entries = Object.entries(manifest.archetypes);
      const loadTier = async (tier: SceneLod) => {
        await Promise.all(
          entries.map(async ([assetKey, entry]) => {
            const lod = entry.lods.find((candidate) => candidate.lod === tier);
            if (!lod) return;
            const url = lod.url;
            try {
              const gltf = await loader.loadAsync(url);
              const prototype = extractPrototype(
                assetKey,
                lod.lod,
                gltf.scene,
                entry.nominalHeightM,
                entry.nominalRadiusM,
              );
              gltf.scene.traverse((object) => {
                if (!(object instanceof THREE.Mesh)) return;
                object.geometry.dispose();
                const materials = Array.isArray(object.material)
                  ? object.material
                  : [object.material];
                materials.forEach((material) => material.dispose());
              });
              if (!prototype) return;
              if (library.disposed) {
                library.disposePrototype(prototype);
                return;
              }
              const key = `${assetKey}:${lod.lod}`;
              const previous = library.prototypes.get(key);
              if (previous) library.disposePrototype(previous);
              library.prototypes.set(key, prototype);
              library.notify({ assetKey, lod: lod.lod, format: 'glb' });
            } catch {
              library.failedUrls.add(url);
            }
          }),
        );
      };
      // A small, complete far tier is the first usable scene. Mid and near
      // geometry stream afterwards and notify the controller to rebucket.
      await loadTier('far');
      // Do not start 3.4 MiB of detail work for a transient 3D mount (quick
      // 2D↔3D switch, route transition, React development probe). Besides
      // saving bandwidth this makes disposal real: GLTFLoader has no fetch
      // AbortSignal and otherwise retains decoded buffers until every pending
      // upgrade settles.
      library.upgradesPromise = new Promise((resolve) => {
        library.resolveUpgrades = resolve;
        library.upgradeTimer = setTimeout(() => {
          library.upgradeTimer = undefined;
          if (library.disposed) {
            library.resolveUpgrades = undefined;
            resolve();
            return;
          }
          void (async () => {
            // A focused/selected tree is more important than completing every
            // intermediate overview asset first. Start both tiers together so
            // a cold near view is not guaranteed to display the far fallback
            // for an additional full mid-tier download.
            await Promise.all([loadTier('near'), loadTier('mid')]);
          })().finally(() => {
            library.resolveUpgrades = undefined;
            resolve();
          });
        }, 1_200);
      });
    } catch {
      library.failedUrls.add(manifestUrl);
    }
    return library;
  }

  assetKey(object: ScenePlantWithAsset) {
    const explicit =
      object.model_variant_key?.trim() ||
      object.asset_key?.trim() ||
      object.species_key?.trim();
    if (explicit && this.speciesMap.has(explicit))
      return this.speciesMap.get(explicit)!;
    const scientificName = object.scientific_name?.trim();
    if (scientificName && this.speciesMap.has(scientificName))
      return this.speciesMap.get(scientificName)!;
    const speciesId = object.species_id?.trim();
    if (speciesId && this.speciesMap.has(speciesId))
      return this.speciesMap.get(speciesId)!;
    const revision = object.species_revision_id?.trim();
    if (revision && this.speciesMap.has(revision))
      return this.speciesMap.get(revision)!;
    if (explicit && this.prototypes.has(`${explicit}:near`)) return explicit;
    return defaultAssetKey(object);
  }

  get(object: ScenePlantWithAsset, lod: SceneLod) {
    let assetKey = this.assetKey(object);
    let prototype = this.prototypes.get(`${assetKey}:${lod}`);
    if (!prototype) {
      assetKey = defaultAssetKey(object);
      prototype = this.prototypes.get(`${assetKey}:${lod}`);
    }
    if (!prototype) {
      const fallbackOrder: SceneLod[] =
        lod === 'near'
          ? ['mid', 'far']
          : lod === 'mid'
            ? ['far', 'near']
            : ['mid', 'near'];
      prototype = fallbackOrder
        .map((fallbackLod) => this.prototypes.get(`${assetKey}:${fallbackLod}`))
        .find((candidate): candidate is PlantPrototype => Boolean(candidate));
    }
    // A missing broadleaf must never silently become a conifer or shrub just
    // because another archetype happened to finish downloading first.
    if (!prototype)
      return {
        assetKey: 'missing-artist-asset',
        lod,
        nominalHeightM: 1,
        nominalRadiusM: 1,
        parts: [],
      };
    return prototype;
  }

  loadedModelCount() {
    return this.prototypes.size;
  }

  failedModelCount() {
    return this.failedUrls.size;
  }

  subscribe(listener: (event: PlantAssetLoadedEvent) => void) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  whenReady() {
    return this.upgradesPromise;
  }

  private notify(event: PlantAssetLoadedEvent) {
    this.listeners.forEach((listener) => listener(event));
  }

  private disposePrototype(prototype: PlantPrototype) {
    const textures = new Set<THREE.Texture>();
    const materials = new Set<THREE.Material>();
    for (const part of prototype.parts) {
      part.geometry.dispose();
      const partMaterials = Array.isArray(part.material)
        ? part.material
        : [part.material];
      for (const material of partMaterials) {
        materials.add(material);
        materialTextures(material).forEach((texture) => textures.add(texture));
      }
    }
    textures.forEach((texture) => {
      const source = texture.source?.data as unknown;
      if (
        source &&
        typeof source === 'object' &&
        'close' in source &&
        typeof source.close === 'function'
      )
        source.close();
      texture.dispose();
    });
    materials.forEach((material) => material.dispose());
  }

  dispose() {
    this.disposed = true;
    if (this.upgradeTimer) clearTimeout(this.upgradeTimer);
    this.upgradeTimer = undefined;
    this.resolveUpgrades?.();
    this.resolveUpgrades = undefined;
    for (const prototype of this.prototypes.values())
      this.disposePrototype(prototype);
    this.prototypes.clear();
    this.failedUrls.clear();
    this.listeners.clear();
  }
}

// Decoded GLB geometry and textures are application assets, not view state.
// Keep one bounded cache across quick 2D↔3D switches so a toggle does not
// re-fetch and re-parse the same four models or create a saw-tooth heap. The
// delayed release still frees the cache after the user leaves 3D for good.
const sharedAssetCacheTtlMs = 30_000;
type AssetCacheEntry = {
  library: Promise<PlantAssetLibrary>;
  assets?: PlantAssetLibrary;
  references: number;
  unavailable: boolean;
  disposed: boolean;
  disposalTimer?: ReturnType<typeof setTimeout>;
};
let sharedAssetEntry: AssetCacheEntry | undefined;

function disposeAssetEntry(entry: AssetCacheEntry) {
  if (entry.disposed) return;
  entry.disposed = true;
  if (entry.disposalTimer) clearTimeout(entry.disposalTimer);
  entry.disposalTimer = undefined;
  if (sharedAssetEntry === entry) sharedAssetEntry = undefined;
  entry.assets?.dispose();
}

function createAssetEntry(
  manifestUrl: string,
  options: PlantAssetLibraryLoadOptions,
): AssetCacheEntry {
  const entry: AssetCacheEntry = {
    library: PlantAssetLibrary.load(manifestUrl, options),
    references: 0,
    unavailable: false,
    disposed: false,
  };
  void entry.library.then((assets) => {
    entry.assets = assets;
    if (entry.disposed) {
      assets.dispose();
      return;
    }
    entry.unavailable = assets.loadedModelCount() === 0;
    if (!entry.unavailable) return;
    // An empty library cannot start a scene. A later explicit retry must load
    // again, while leases from the failed attempt retain their own lifetime.
    if (sharedAssetEntry === entry) sharedAssetEntry = undefined;
    if (!entry.references) disposeAssetEntry(entry);
  });
  return entry;
}

export function acquirePlantAssetLibrary(
  manifestUrl = '/assets/plant-models/manifest.json',
  options: PlantAssetLibraryLoadOptions = {},
): PlantAssetLibraryLease {
  const entry = (sharedAssetEntry ??= createAssetEntry(manifestUrl, options));
  entry.references += 1;
  if (entry.disposalTimer) clearTimeout(entry.disposalTimer);
  entry.disposalTimer = undefined;
  let released = false;

  return {
    library: entry.library,
    release() {
      if (released) return;
      released = true;
      entry.references = Math.max(0, entry.references - 1);
      if (entry.references || entry.disposalTimer) return;
      if (entry.unavailable) {
        disposeAssetEntry(entry);
        return;
      }
      entry.disposalTimer = setTimeout(() => {
        entry.disposalTimer = undefined;
        if (!entry.references) disposeAssetEntry(entry);
      }, sharedAssetCacheTtlMs);
    },
  };
}
