/** Отрисовка посадок в 3D: экземпляры, выбор объектов и рабочие корневые зоны. */
import * as THREE from 'three';
import type { ScenePlantObject } from '@green/api-client';
import { workingEnvelopeRadius } from '@/entities/planting-forecast/model/growthForecast';
import { OutlinePass } from 'three/examples/jsm/postprocessing/OutlinePass.js';
import type { PlantPrototype } from './plantAssets';
import {
  SCENE_RENDER_TARGETS,
  type SceneLod,
} from '../../model/sceneRenderContract';

export type PlantInstance = {
  object: ScenePlantObject;
  prototype: PlantPrototype;
  lod: SceneLod;
  matrix: THREE.Matrix4;
};

type InstanceBucket = {
  prototype: PlantPrototype;
  partIndex: number;
  items: PlantInstance[];
};

// Visual fallbacks do not supply missing engineering height measurements.
const PLANT_RENDERING = {
  minimumCanopyRadiusM: 0.18,
  minimumPrototypeDimensionM: 0.01,
  tree: { minimumHeightM: 1.8, heightToRadiusRatio: 2.55 },
  shrub: { minimumHeightM: 0.35, heightToRadiusRatio: 1.25 },
};
const FNV_OFFSET_BASIS = 2166136261;
const FNV_PRIME = 16777619;
const UINT32_MAX = 0xffffffff;
const selectedColor = new THREE.Color(0x1455ff);
const errorColor = new THREE.Color(0xdf4237);
const warningColor = new THREE.Color(0xd48218);
const hoverColor = new THREE.Color(0xf7fbff);
const tempVector = new THREE.Vector3();
const tempScale = new THREE.Vector3();
const tempQuaternion = new THREE.Quaternion();
function deterministicAngle(id: string) {
  let hash = FNV_OFFSET_BASIS;
  for (let index = 0; index < id.length; index += 1) {
    hash ^= id.charCodeAt(index);
    hash = Math.imul(hash, FNV_PRIME);
  }
  return ((hash >>> 0) / UINT32_MAX) * Math.PI * 2;
}

export function plantMatrix(
  object: ScenePlantObject,
  prototype: PlantPrototype,
  elevation = 0,
  rotationOverride?: number,
) {
  const fallback =
    object.kind === 'tree' ? PLANT_RENDERING.tree : PLANT_RENDERING.shrub;
  const targetRadius = Math.max(
    PLANT_RENDERING.minimumCanopyRadiusM,
    object.canopy_radius_max_m,
  );
  const targetHeight = Math.max(
    fallback.minimumHeightM,
    object.height_max_m ?? targetRadius * fallback.heightToRadiusRatio,
  );
  tempScale.set(
    targetRadius /
      Math.max(
        PLANT_RENDERING.minimumPrototypeDimensionM,
        prototype.nominalRadiusM,
      ),
    targetHeight /
      Math.max(
        PLANT_RENDERING.minimumPrototypeDimensionM,
        prototype.nominalHeightM,
      ),
    targetRadius /
      Math.max(
        PLANT_RENDERING.minimumPrototypeDimensionM,
        prototype.nominalRadiusM,
      ),
  );
  tempQuaternion.setFromAxisAngle(
    THREE.Object3D.DEFAULT_UP,
    rotationOverride ?? deterministicAngle(object.object_id),
  );
  return new THREE.Matrix4().compose(
    tempVector.set(object.local_x, elevation, -object.local_y),
    tempQuaternion,
    tempScale,
  );
}

function disposeInstancedWrappers(root: THREE.Group) {
  for (const child of [...root.children]) {
    root.remove(child);
    if (child instanceof THREE.InstancedMesh) {
      child.dispose();
      if (child.userData.disposeMaterial) {
        const materials = Array.isArray(child.material)
          ? child.material
          : [child.material];
        materials.forEach((material) => material.dispose());
      }
    }
  }
}

function sourceMaterialAt(
  material: THREE.Material | THREE.Material[],
  index = 0,
) {
  return Array.isArray(material) ? (material[index] ?? material[0]) : material;
}

function alphaPickingMaterial(
  source: THREE.Material,
  whiteTexture: THREE.Texture,
) {
  const textured = source as THREE.Material & {
    map?: THREE.Texture | null;
    alphaMap?: THREE.Texture | null;
  };
  return new THREE.ShaderMaterial({
    uniforms: {
      plantMap: { value: textured.map ?? whiteTexture },
      plantAlphaMap: { value: textured.alphaMap ?? whiteTexture },
      plantAlphaTest: { value: source.alphaTest },
    },
    vertexShader: `
      #include <common>
      #include <logdepthbuf_pars_vertex>
      varying vec3 pickColor;
      varying vec2 plantUv;
      void main() {
        pickColor = instanceColor;
        plantUv = uv;
        gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0);
        #include <logdepthbuf_vertex>
      }
    `,
    fragmentShader: `
      #include <logdepthbuf_pars_fragment>
      varying vec3 pickColor;
      varying vec2 plantUv;
      uniform sampler2D plantMap;
      uniform sampler2D plantAlphaMap;
      uniform float plantAlphaTest;
      void main() {
        float alpha = texture2D(plantMap, plantUv).a * texture2D(plantAlphaMap, plantUv).g;
        if (alpha < plantAlphaTest) discard;
        gl_FragColor = vec4(pickColor, 1.0);
        #include <logdepthbuf_fragment>
      }
    `,
    side: source.side,
    toneMapped: false,
  });
}

// Exported for deterministic regression tests; zero remains the background.
export function pickingColorForIndex(index: number): [number, number, number] {
  const encoded = index + 1;
  return [
    (encoded & 255) / 255,
    ((encoded >> 8) & 255) / 255,
    ((encoded >> 16) & 255) / 255,
  ];
}

export function pickingIndexFromPixel(pixel: ArrayLike<number>) {
  const encoded = pixel[0] | (pixel[1] << 8) | (pixel[2] << 16);
  return encoded > 0 ? encoded - 1 : undefined;
}

function instanceBuckets(instances: PlantInstance[]) {
  const buckets = new Map<string, InstanceBucket>();
  for (const instance of instances) {
    instance.prototype.parts.forEach((_part, partIndex) => {
      const key = `${instance.prototype.assetKey}:${instance.lod}:${partIndex}`;
      const bucket = buckets.get(key);
      if (bucket) bucket.items.push(instance);
      else
        buckets.set(key, {
          prototype: instance.prototype,
          partIndex,
          items: [instance],
        });
    });
  }

  return buckets;
}
/** Owns instance buffers and picking/outline resources, never the shared plant
 * asset geometry/materials or the editor selection. */
export class ScenePlantLayers {
  readonly pickScene = new THREE.Scene();
  readonly plantRoot = new THREE.Group();
  readonly pickRoot = new THREE.Group();
  readonly rootsRoot = new THREE.Group();
  readonly outlineRoots = {
    selected: new THREE.Group(),
    error: new THREE.Group(),
    warning: new THREE.Group(),
    hover: new THREE.Group(),
  };
  readonly outlineProxyMaterial = new THREE.MeshBasicMaterial({
    color: 0xffffff,
    colorWrite: false,
    depthWrite: false,
    toneMapped: false,
  });
  readonly outlinePass: OutlinePass;
  readonly rootGeometry = new THREE.RingGeometry(0.66, 1, 48).rotateX(
    -Math.PI / 2,
  );
  readonly pickTarget = new THREE.WebGLRenderTarget(1, 1, {
    depthBuffer: true,
    stencilBuffer: false,
  });
  readonly pickPixel = new Uint8Array(4);
  readonly pickIds: string[] = [];
  readonly whiteAlphaTexture = new THREE.DataTexture(
    new Uint8Array([255, 255, 255, 255]),
    1,
    1,
  );
  readonly rootMaterial = new THREE.MeshBasicMaterial({
    color: 0xa76628,
    transparent: true,
    opacity: 0.24,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  readonly billboardPosition = new THREE.Vector3();
  readonly billboardScale = new THREE.Vector3();
  readonly billboardSourceQuaternion = new THREE.Quaternion();
  readonly billboardMatrix = new THREE.Matrix4();
  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly camera: THREE.PerspectiveCamera,
    private readonly renderer: Pick<
      THREE.WebGLRenderer,
      | 'getRenderTarget'
      | 'getClearColor'
      | 'getClearAlpha'
      | 'setRenderTarget'
      | 'setClearColor'
      | 'clear'
      | 'render'
      | 'readRenderTargetPixels'
    >,
    private readonly scene: THREE.Scene,
  ) {
    this.outlinePass = new OutlinePass(
      new THREE.Vector2(1, 1),
      scene,
      camera,
      [],
    );
    this.outlinePass.edgeStrength = 5.2;
    this.outlinePass.edgeGlow = 0;
    this.outlinePass.edgeThickness = 3;
    this.outlinePass.pulsePeriod = 0;
    this.outlinePass.visibleEdgeColor.copy(selectedColor);
    this.outlinePass.hiddenEdgeColor.copy(selectedColor).multiplyScalar(0.42);
    this.configureAlphaAwareOutlineMask();
  }

  attach() {
    this.plantRoot.name = 'plant-instances';
    this.pickRoot.name = 'plant-hit-volumes';
    this.pickScene.add(this.pickRoot);
    this.rootsRoot.name = 'root-zones';
    Object.values(this.outlineRoots).forEach((root) => {
      root.name = 'outline-proxies';
      this.scene.add(root);
    });
  }

  refreshBillboards() {
    [
      this.plantRoot,
      this.pickRoot,
      ...Object.values(this.outlineRoots),
    ].forEach((root) => this.refreshRootBillboards(root));
  }

  clearInstances() {
    [
      this.plantRoot,
      this.pickRoot,
      this.rootsRoot,
      ...Object.values(this.outlineRoots),
    ].forEach(disposeInstancedWrappers);
  }

  dispose() {
    this.outlinePass.dispose();
    this.outlineProxyMaterial.dispose();
    this.rootGeometry.dispose();
    this.rootMaterial.dispose();
    this.pickTarget.dispose();
    this.whiteAlphaTexture.dispose();
  }
  private configureAlphaAwareOutlineMask() {
    const mask = this.outlinePass.prepareMaskMaterial;
    this.whiteAlphaTexture.needsUpdate = true;
    mask.uniforms.plantMap = { value: this.whiteAlphaTexture };
    mask.uniforms.plantAlphaMap = { value: this.whiteAlphaTexture };
    mask.uniforms.plantAlphaTest = { value: 0 };
    mask.vertexShader = mask.vertexShader
      .replace(
        'varying vec4 projTexCoord;',
        'varying vec4 projTexCoord;\nvarying vec2 plantUv;',
      )
      .replace('void main() {', 'void main() {\nplantUv = uv;');
    mask.fragmentShader = mask.fragmentShader
      .replace(
        'uniform vec2 cameraNearFar;',
        'uniform vec2 cameraNearFar;\nvarying vec2 plantUv;\nuniform sampler2D plantMap;\nuniform sampler2D plantAlphaMap;\nuniform float plantAlphaTest;',
      )
      .replace(
        'void main() {',
        'void main() {\nfloat plantAlpha = texture2D( plantMap, plantUv ).a * texture2D( plantAlphaMap, plantUv ).g;\nif ( plantAlpha < plantAlphaTest ) discard;',
      );
    mask.needsUpdate = true;
  }

  private matrixForPart(instance: PlantInstance, billboard: boolean) {
    if (!billboard) return instance.matrix;
    instance.matrix.decompose(
      this.billboardPosition,
      this.billboardSourceQuaternion,
      this.billboardScale,
    );
    // Spherical billboarding keeps the complete tree silhouette legible from
    // both pedestrian and aerial cameras. The source card is ground-anchored,
    // so rotating its local Y axis also preserves the visible base naturally.
    return this.billboardMatrix.compose(
      this.billboardPosition,
      this.camera.quaternion,
      this.billboardScale,
    );
  }

  private refreshRootBillboards(root: THREE.Group) {
    root.traverse((object) => {
      if (
        !(object instanceof THREE.InstancedMesh) ||
        !object.userData.billboard
      )
        return;
      const items = object.userData.plantItems as PlantInstance[] | undefined;
      if (!items) return;
      items.forEach((item, index) =>
        object.setMatrixAt(index, this.matrixForPart(item, true)),
      );
      object.instanceMatrix.needsUpdate = true;
    });
  }

  rebuild(instances: PlantInstance[]) {
    disposeInstancedWrappers(this.plantRoot);
    disposeInstancedWrappers(this.pickRoot);
    const buckets = instanceBuckets(instances);
    this.pickIds.length = 0;
    this.pickIds.push(...instances.map((item) => item.object.object_id));
    const pickIndex = new Map(this.pickIds.map((id, index) => [id, index + 1]));
    for (const [key, bucket] of buckets) {
      const part = bucket.prototype.parts[bucket.partIndex];
      for (
        let offset = 0;
        offset < bucket.items.length;
        offset += SCENE_RENDER_TARGETS.maxInstancesPerBatch
      ) {
        const items = bucket.items.slice(
          offset,
          offset + SCENE_RENDER_TARGETS.maxInstancesPerBatch,
        );
        const mesh = new THREE.InstancedMesh(
          part.geometry,
          part.material,
          items.length,
        );
        mesh.name = `plants:${key}:${offset}`;
        mesh.userData.objectIds = items.map((item) => item.object.object_id);
        mesh.userData.plantBatch = true;
        mesh.userData.billboard = part.billboard;
        mesh.userData.plantItems = items;
        mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
        items.forEach((item, index) =>
          mesh.setMatrixAt(index, this.matrixForPart(item, part.billboard)),
        );
        mesh.instanceMatrix.needsUpdate = true;
        mesh.castShadow = part.castShadow && bucket.prototype.lod !== 'far';
        mesh.receiveShadow = (
          Array.isArray(part.material) ? part.material : [part.material]
        ).some((material) => !(material instanceof THREE.MeshBasicMaterial));
        mesh.computeBoundingBox();
        mesh.computeBoundingSphere();
        // A camera-facing card rotates beyond its initial world-space AABB.
        // Avoid recomputing thousands of bounds every frame; the controller's
        // own distance/frustum LOD already decides which instances are present.
        if (part.billboard) mesh.frustumCulled = false;
        this.plantRoot.add(mesh);

        const sourceMaterials = Array.isArray(part.material)
          ? part.material
          : [part.material];
        const pickMaterials = sourceMaterials.map((material) =>
          alphaPickingMaterial(material, this.whiteAlphaTexture),
        );
        const pickMesh = new THREE.InstancedMesh(
          part.geometry,
          pickMaterials.length === 1 ? pickMaterials[0] : pickMaterials,
          items.length,
        );
        pickMesh.name = `plant-pick:${key}:${offset}`;
        pickMesh.userData.disposeMaterial = true;
        pickMesh.userData.billboard = part.billboard;
        pickMesh.userData.plantItems = items;
        pickMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
        items.forEach((item, index) => {
          pickMesh.setMatrixAt(index, this.matrixForPart(item, part.billboard));
          const encodedIndex = (pickIndex.get(item.object.object_id) ?? 1) - 1;
          pickMesh.setColorAt(
            index,
            new THREE.Color(...pickingColorForIndex(encodedIndex)),
          );
        });
        pickMesh.instanceMatrix.needsUpdate = true;
        if (pickMesh.instanceColor) pickMesh.instanceColor.needsUpdate = true;
        pickMesh.frustumCulled = false;
        this.pickRoot.add(pickMesh);
      }
    }
  }

  rebuildRoots(
    instances: PlantInstance[],
    rootsVisible: boolean,
    terrainElevationAt: (x: number, z: number) => number,
  ) {
    disposeInstancedWrappers(this.rootsRoot);
    if (!rootsVisible) return;
    const withRoots = instances.flatMap((instance) => {
      const radius = workingEnvelopeRadius('roots', {
        radius_min_m: instance.object.root_radius_min_m ?? undefined,
      });
      return radius !== undefined && radius > 0 ? [{ instance, radius }] : [];
    });
    if (!withRoots.length) return;
    const mesh = new THREE.InstancedMesh(
      this.rootGeometry,
      this.rootMaterial,
      withRoots.length,
    );
    const matrix = new THREE.Matrix4();
    withRoots.forEach(({ instance, radius }, index) => {
      const terrainY = terrainElevationAt(
        instance.object.local_x,
        -instance.object.local_y,
      );
      matrix.compose(
        tempVector.set(
          instance.object.local_x,
          terrainY + 0.025,
          -instance.object.local_y,
        ),
        new THREE.Quaternion(),
        tempScale.set(radius, radius, radius),
      );
      mesh.setMatrixAt(index, matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
    mesh.name = 'root-zones:instanced';
    mesh.renderOrder = 3;
    this.rootsRoot.add(mesh);
  }

  rebuildOutlines(
    instances: PlantInstance[],
    selectedIds: ReadonlySet<string>,
    hoveredId: string | undefined,
    issueSeverity: ReadonlyMap<string, 'error' | 'warning'>,
  ) {
    Object.values(this.outlineRoots).forEach(disposeInstancedWrappers);
    const groups: Record<keyof typeof this.outlineRoots, PlantInstance[]> = {
      selected: [],
      error: [],
      warning: [],
      hover: [],
    };
    for (const instance of instances) {
      const id = instance.object.object_id;
      if (selectedIds.has(id)) groups.selected.push(instance);
      else if (hoveredId === id) {
        const severity = issueSeverity.get(id);
        if (severity === 'error') groups.error.push(instance);
        else if (severity === 'warning') groups.warning.push(instance);
        else groups.hover.push(instance);
      }
    }
    const active = groups.selected.length
      ? { kind: 'selected' as const, color: selectedColor, strength: 5.2 }
      : groups.hover.length
        ? { kind: 'hover' as const, color: hoverColor, strength: 3.8 }
        : groups.error.length
          ? { kind: 'error' as const, color: errorColor, strength: 4.6 }
          : groups.warning.length
            ? { kind: 'warning' as const, color: warningColor, strength: 4.2 }
            : undefined;
    if (!active) {
      this.outlinePass.selectedObjects = [];
      return;
    }
    const root = this.outlineRoots[active.kind];
    this.buildOutlineInstances(root, groups[active.kind]);
    this.outlinePass.visibleEdgeColor.copy(active.color);
    this.outlinePass.hiddenEdgeColor.copy(active.color).multiplyScalar(0.42);
    this.outlinePass.edgeStrength = active.strength;
    this.outlinePass.selectedObjects = [root];
  }

  private buildOutlineInstances(root: THREE.Group, instances: PlantInstance[]) {
    if (!instances.length) return;
    const buckets = instanceBuckets(instances);
    for (const bucket of buckets.values()) {
      const part = bucket.prototype.parts[bucket.partIndex];
      const originals = Array.isArray(part.material)
        ? part.material
        : [part.material];
      const mesh = new THREE.InstancedMesh(
        part.geometry,
        originals.length === 1
          ? this.outlineProxyMaterial
          : originals.map(() => this.outlineProxyMaterial),
        bucket.items.length,
      );
      mesh.userData.billboard = part.billboard;
      mesh.userData.plantItems = bucket.items;
      bucket.items.forEach((instance, index) =>
        mesh.setMatrixAt(index, this.matrixForPart(instance, part.billboard)),
      );
      mesh.instanceMatrix.needsUpdate = true;
      mesh.frustumCulled = false;
      mesh.renderOrder = 8;
      mesh.onBeforeRender = (
        _renderer,
        _scene,
        _camera,
        _geometry,
        _material,
        group,
      ) => {
        const materialIndex =
          (group as unknown as { materialIndex?: number } | null)
            ?.materialIndex ?? 0;
        const source = sourceMaterialAt(part.material, materialIndex);
        const textured = source as THREE.Material & {
          map?: THREE.Texture | null;
          alphaMap?: THREE.Texture | null;
        };
        this.outlinePass.prepareMaskMaterial.uniforms.plantMap.value =
          textured.map ?? this.whiteAlphaTexture;
        this.outlinePass.prepareMaskMaterial.uniforms.plantAlphaMap.value =
          textured.alphaMap ?? this.whiteAlphaTexture;
        this.outlinePass.prepareMaskMaterial.uniforms.plantAlphaTest.value =
          source.alphaTest;
      };
      root.add(mesh);
    }
  }

  objectAt(event: { clientX: number; clientY: number }) {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height || !this.pickIds.length) return undefined;
    const pixelX = THREE.MathUtils.clamp(
      Math.floor(event.clientX - rect.left),
      0,
      Math.floor(rect.width) - 1,
    );
    const pixelY = THREE.MathUtils.clamp(
      Math.floor(event.clientY - rect.top),
      0,
      Math.floor(rect.height) - 1,
    );
    const previousTarget = this.renderer.getRenderTarget();
    const previousColor = this.renderer.getClearColor(new THREE.Color());
    const previousAlpha = this.renderer.getClearAlpha();
    this.camera.setViewOffset(
      Math.floor(rect.width),
      Math.floor(rect.height),
      pixelX,
      pixelY,
      1,
      1,
    );
    this.renderer.setRenderTarget(this.pickTarget);
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.clear(true, true, true);
    this.renderer.render(this.pickScene, this.camera);
    this.renderer.readRenderTargetPixels(
      this.pickTarget,
      0,
      0,
      1,
      1,
      this.pickPixel,
    );
    this.camera.clearViewOffset();
    this.renderer.setRenderTarget(previousTarget);
    this.renderer.setClearColor(previousColor, previousAlpha);
    const index = pickingIndexFromPixel(this.pickPixel);
    return index === undefined ? undefined : this.pickIds[index];
  }
}
