import type { ScenePlantObject, SceneSnapshot, ValidationIssue } from '@green/api-client';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { PlantAssetLibrary, type PlantPrototype } from './plantAssets';
import { buildContextScene, disposeObjectTree, type SceneContextLike } from './sceneGeometry';
import {
  classifySceneLod,
  SCENE_ACCESSIBILITY_CONTRACT,
  SCENE_RENDER_TARGETS,
  type SceneLod,
  type SceneRenderTelemetry,
} from './sceneRenderContract';

type SceneSnapshotWithContext = SceneSnapshot & {
  context_features?: SceneContextLike[];
};

type SceneCameraMode = 'overview' | 'ground';
type SceneHover = { objectId?: string; clientX: number; clientY: number };

export type SceneControllerOptions = {
  canvas: HTMLCanvasElement;
  assetLibrary: PlantAssetLibrary;
  onSelect: (objectId: string) => void;
  onHover?: (hover: SceneHover) => void;
  onTelemetry?: (telemetry: SceneRenderTelemetry) => void;
};

type PlantInstance = {
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

const selectedColor = new THREE.Color(0x1455ff);
const errorColor = new THREE.Color(0xdf4237);
const warningColor = new THREE.Color(0xd48218);
const hoverColor = new THREE.Color(0xf7fbff);
const tempVector = new THREE.Vector3();
const tempScale = new THREE.Vector3();
const tempQuaternion = new THREE.Quaternion();
let rendererGenerationSequence = 0;

function deterministicAngle(id: string) {
  let hash = 2166136261;
  for (let index = 0; index < id.length; index += 1) {
    hash ^= id.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return ((hash >>> 0) / 0xffffffff) * Math.PI * 2;
}

function plantMatrix(object: ScenePlantObject, prototype: PlantPrototype) {
  const targetRadius = Math.max(0.18, object.canopy_radius_max_m);
  const targetHeight = Math.max(
    object.kind === 'tree' ? 1.8 : 0.35,
    object.height_max_m ?? (object.kind === 'tree' ? targetRadius * 2.55 : targetRadius * 1.25),
  );
  tempScale.set(
    targetRadius / Math.max(0.01, prototype.nominalRadiusM),
    targetHeight / Math.max(0.01, prototype.nominalHeightM),
    targetRadius / Math.max(0.01, prototype.nominalRadiusM),
  );
  tempQuaternion.setFromAxisAngle(THREE.Object3D.DEFAULT_UP, deterministicAngle(object.object_id));
  return new THREE.Matrix4().compose(
    tempVector.set(object.local_x, 0, -object.local_y),
    tempQuaternion,
    tempScale,
  );
}

function severityByObject(issues: ValidationIssue[]) {
  const result = new Map<string, ValidationIssue['severity']>();
  for (const issue of issues) {
    if (!issue.object_id) continue;
    const previous = result.get(issue.object_id);
    if (!previous || issue.severity === 'error') result.set(issue.object_id, issue.severity);
  }
  return result;
}

function disposeInstancedWrappers(root: THREE.Group) {
  for (const child of [...root.children]) {
    root.remove(child);
    if (child instanceof THREE.InstancedMesh) child.dispose();
  }
}

function makeOutlineMaterial(color: THREE.Color, thicknessPx: number) {
  return new THREE.ShaderMaterial({
    uniforms: {
      outlineColor: { value: color },
      resolution: { value: new THREE.Vector2(1, 1) },
      thicknessPx: { value: thicknessPx },
    },
    vertexShader: `
      uniform vec2 resolution;
      uniform float thicknessPx;
      void main() {
        vec4 localPosition = vec4(position, 1.0);
        vec3 localNormal = normal;
        #ifdef USE_INSTANCING
          localPosition = instanceMatrix * localPosition;
          vec3 instanceScale = vec3(
            length(instanceMatrix[0].xyz),
            length(instanceMatrix[1].xyz),
            length(instanceMatrix[2].xyz)
          );
          localNormal = mat3(instanceMatrix) * (localNormal / max(instanceScale * instanceScale, vec3(0.00001)));
        #endif
        vec4 clip = projectionMatrix * modelViewMatrix * localPosition;
        vec3 viewNormal = normalize(normalMatrix * localNormal);
        vec2 projectedNormal = (projectionMatrix * vec4(viewNormal, 0.0)).xy;
        float normalLength = max(length(projectedNormal), 0.00001);
        vec2 pixelOffset = projectedNormal / normalLength * (2.0 * thicknessPx / resolution);
        clip.xy += pixelOffset * clip.w;
        gl_Position = clip;
      }
    `,
    fragmentShader: `
      uniform vec3 outlineColor;
      void main() {
        gl_FragColor = vec4(outlineColor, 1.0);
        #include <colorspace_fragment>
      }
    `,
    side: THREE.BackSide,
    depthTest: true,
    depthWrite: false,
    toneMapped: false,
  });
}

export class SceneController {
  private readonly canvas: HTMLCanvasElement;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(42, 1, 0.08, 20_000);
  private readonly controls: OrbitControls;
  private readonly plantRoot = new THREE.Group();
  private readonly contextRoot = new THREE.Group();
  private readonly rootsRoot = new THREE.Group();
  private readonly outlineRoots = {
    selected: new THREE.Group(),
    error: new THREE.Group(),
    warning: new THREE.Group(),
    hover: new THREE.Group(),
  };
  private readonly outlineMaterials = {
    selected: makeOutlineMaterial(selectedColor, SCENE_ACCESSIBILITY_CONTRACT.outlineWidthPx),
    error: makeOutlineMaterial(errorColor, 1.6),
    warning: makeOutlineMaterial(warningColor, 1.35),
    hover: makeOutlineMaterial(hoverColor, 1.25),
  };
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly objectById = new Map<string, ScenePlantObject>();
  private readonly previousLod = new Map<string, SceneLod>();
  private currentInstances: PlantInstance[] = [];
  private readonly selectedIds = new Set<string>();
  private readonly issueSeverity = new Map<string, ValidationIssue['severity']>();
  private readonly frameSamples: number[] = [];
  private readonly resizeObserver: ResizeObserver;
  private readonly onSelect: (objectId: string) => void;
  private readonly onHover?: (hover: SceneHover) => void;
  private readonly onTelemetry?: (telemetry: SceneRenderTelemetry) => void;
  private assetLibrary: PlantAssetLibrary;
  private snapshot?: SceneSnapshotWithContext;
  private rootsVisible = false;
  private hoveredId?: string;
  private frame = 0;
  private lodFrame = 0;
  private lastLodRebuildAt = 0;
  private telemetryFrame = 0;
  private animationStartedAt = 0;
  private cameraAnimation?: {
    startedAt: number;
    duration: number;
    fromPosition: THREE.Vector3;
    toPosition: THREE.Vector3;
    fromTarget: THREE.Vector3;
    toTarget: THREE.Vector3;
  };
  private lastFrameAt = performance.now();
  private readonly rendererGeneration = ++rendererGenerationSequence;
  private visiblePlantInstances = 0;
  private disposed = false;

  constructor(options: SceneControllerOptions) {
    this.canvas = options.canvas;
    this.assetLibrary = options.assetLibrary;
    this.onSelect = options.onSelect;
    this.onHover = options.onHover;
    this.onTelemetry = options.onTelemetry;

    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
      alpha: false,
      powerPreference: 'high-performance',
      preserveDrawingBuffer: false,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, SCENE_RENDER_TARGETS.maxDevicePixelRatio));
    this.renderer.setClearColor(0xdfe8e5, 1);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.02;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    // Reset explicitly once per frame so diagnostics remain comparable when
    // outline batches appear or disappear.
    this.renderer.info.autoReset = false;

    this.scene.background = new THREE.Color(0xdfe8e5);
    // Atmospheric depth should separate planes, not erase a city-scale plan.
    // A denser value made the truthful DXF context and young plants disappear
    // in the default overview on 700–1000 m scenes.
    this.scene.fog = new THREE.FogExp2(0xdfe8e5, 0.00035);
    this.camera.position.set(36, 31, 42);

    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.075;
    this.controls.maxPolarAngle = Math.PI / 2.02;
    this.controls.minDistance = 1.2;
    this.controls.screenSpacePanning = true;
    this.controls.zoomToCursor = true;
    this.controls.target.set(0, 0, 0);
    this.controls.update();

    this.plantRoot.name = 'plant-instances';
    this.contextRoot.name = 'context-root';
    this.rootsRoot.name = 'root-zones';
    Object.values(this.outlineRoots).forEach((root) => {
      root.name = 'outline-proxies';
      this.scene.add(root);
    });
    this.scene.add(this.contextRoot, this.plantRoot, this.rootsRoot);
    this.addEnvironment();

    this.canvas.addEventListener('click', this.handleClick);
    this.canvas.addEventListener('dblclick', this.handleDoubleClick);
    this.canvas.addEventListener('pointermove', this.handlePointerMove, { passive: true });
    this.canvas.addEventListener('pointerleave', this.handlePointerLeave, { passive: true });
    this.controls.addEventListener('start', this.handleControlStart);
    this.controls.addEventListener('change', this.handleControlChange);
    this.controls.addEventListener('end', this.handleControlEnd);
    this.resizeObserver = new ResizeObserver(this.resize);
    this.resizeObserver.observe(this.canvas);
    this.resize();
    this.requestRender(280);
  }

  private addEnvironment() {
    this.scene.add(new THREE.HemisphereLight(0xf2f7f2, 0x68766e, 2.25));
    const sun = new THREE.DirectionalLight(0xfff3df, 2.6);
    sun.name = 'sun';
    sun.position.set(-70, 110, 45);
    sun.castShadow = true;
    sun.shadow.mapSize.set(1536, 1536);
    sun.shadow.bias = -0.00015;
    sun.shadow.normalBias = 0.025;
    sun.shadow.camera.near = 1;
    sun.shadow.camera.far = 360;
    sun.shadow.camera.left = -95;
    sun.shadow.camera.right = 95;
    sun.shadow.camera.top = 95;
    sun.shadow.camera.bottom = -95;
    this.scene.add(sun);

    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(12_000, 12_000),
      new THREE.MeshStandardMaterial({ color: 0xb9c8b7, roughness: 1, metalness: 0 }),
    );
    ground.name = 'reference-ground';
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.045;
    ground.receiveShadow = true;
    this.scene.add(ground);

    const grid = new THREE.GridHelper(12_000, 1_200, 0x7e9189, 0x9caea6);
    grid.name = 'scale-grid';
    const gridMaterials = Array.isArray(grid.material) ? grid.material : [grid.material];
    gridMaterials.forEach((material) => {
      material.transparent = true;
      material.opacity = 0.19;
      material.depthWrite = false;
    });
    grid.position.y = -0.025;
    this.scene.add(grid);
  }

  updateSnapshot(snapshot: SceneSnapshotWithContext, issues: ValidationIssue[] = []) {
    const firstSnapshot = !this.snapshot;
    this.snapshot = snapshot;
    this.objectById.clear();
    snapshot.objects.forEach((object) => this.objectById.set(object.object_id, object));
    this.issueSeverity.clear();
    severityByObject(issues).forEach((severity, id) => this.issueSeverity.set(id, severity));

    for (const child of [...this.contextRoot.children]) {
      this.contextRoot.remove(child);
      disposeObjectTree(child);
    }
    const context = buildContextScene(snapshot.context_features ?? []);
    this.contextRoot.add(context.group);
    this.rebuildPlants();
    if (firstSnapshot) this.fitAll(false);
    this.requestRender(420);
  }

  updateIssues(issues: ValidationIssue[]) {
    this.issueSeverity.clear();
    severityByObject(issues).forEach((severity, id) => this.issueSeverity.set(id, severity));
    this.rebuildOutlines();
    this.requestRender(120);
  }

  updateSelection(ids: string[]) {
    this.selectedIds.clear();
    ids.forEach((id) => this.selectedIds.add(id));
    this.rebuildOutlines();
    this.requestRender(160);
  }

  setRootsVisible(visible: boolean) {
    if (this.rootsVisible === visible) return;
    this.rootsVisible = visible;
    this.rebuildRoots();
    this.requestRender(180);
  }

  setCameraMode(mode: SceneCameraMode) {
    if (mode === 'overview') this.fitAll(true);
    else this.focusSelection(true);
  }

  fitAll(animate = true) {
    if (!this.snapshot) return;
    const bounds = new THREE.Box3();
    for (const object of this.snapshot.objects) {
      const radius = Math.max(0.4, object.canopy_radius_max_m);
      bounds.expandByPoint(new THREE.Vector3(object.local_x - radius, 0, -object.local_y - radius));
      bounds.expandByPoint(new THREE.Vector3(object.local_x + radius, object.height_max_m ?? radius * 2.5, -object.local_y + radius));
    }
    // When plants exist, the useful overview is the planting operation with
    // surrounding DXF context—not the entire imported sheet. The latter can
    // be substantially larger and used to reduce every young tree to a pixel.
    if (!this.snapshot.objects.length) {
      this.contextRoot.updateWorldMatrix(true, true);
      bounds.union(new THREE.Box3().setFromObject(this.contextRoot));
    }
    if (bounds.isEmpty()) bounds.setFromCenterAndSize(new THREE.Vector3(), new THREE.Vector3(40, 5, 40));
    const unpaddedSize = bounds.getSize(new THREE.Vector3());
    bounds.expandByScalar(Math.max(12, Math.max(unpaddedSize.x, unpaddedSize.z) * 0.1));
    const center = bounds.getCenter(new THREE.Vector3());
    const size = bounds.getSize(new THREE.Vector3());
    const span = Math.max(20, size.x, size.z, size.y * 2.4);
    const target = new THREE.Vector3(center.x, Math.min(size.y * 0.1, 5), center.z);
    const position = new THREE.Vector3(center.x + span * 0.58, Math.max(18, span * 0.52), center.z + span * 0.7);
    this.controls.maxDistance = Math.max(80, span * 4);
    this.camera.far = Math.max(2_000, span * 12);
    this.camera.updateProjectionMatrix();
    this.moveCamera(position, target, animate ? 680 : 0);
  }

  focusSelection(groundLevel = false) {
    const selected = [...this.selectedIds].map((id) => this.objectById.get(id)).find(Boolean);
    if (!selected) {
      this.fitAll(true);
      return;
    }
    const height = Math.max(2, selected.height_max_m ?? selected.canopy_radius_max_m * 2.5);
    const radius = Math.max(0.6, selected.canopy_radius_max_m);
    const target = new THREE.Vector3(selected.local_x, height * 0.45, -selected.local_y);
    const heading = new THREE.Vector3(0.82, groundLevel ? 0.22 : 0.58, 1).normalize();
    const distance = groundLevel
      ? Math.max(radius * 4, height * 1.7, 7)
      : Math.max(radius * 4.2, height * 1.25, 7);
    const position = target.clone().addScaledVector(heading, distance);
    position.y = Math.max(1.7, position.y);
    this.moveCamera(position, target, 620);
  }

  private moveCamera(position: THREE.Vector3, target: THREE.Vector3, duration: number) {
    if (!duration) {
      this.camera.position.copy(position);
      this.controls.target.copy(target);
      this.controls.update();
      this.rebuildPlants();
      this.requestRender(120);
      return;
    }
    this.cameraAnimation = {
      startedAt: performance.now(),
      duration,
      fromPosition: this.camera.position.clone(),
      toPosition: position,
      fromTarget: this.controls.target.clone(),
      toTarget: target,
    };
    this.requestRender(duration + 120);
  }

  private projectedDiameter(object: ScenePlantObject) {
    const rect = this.canvas.getBoundingClientRect();
    const position = tempVector.set(object.local_x, (object.height_max_m ?? 2) / 2, -object.local_y);
    const distance = Math.max(0.1, this.camera.position.distanceTo(position));
    const visibleHeight = 2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2) * distance;
    return ((object.canopy_radius_max_m * 2) / visibleHeight) * Math.max(1, rect.height);
  }

  private visibleObjects() {
    if (!this.snapshot) return [];
    this.camera.updateMatrixWorld();
    const projection = new THREE.Matrix4().multiplyMatrices(this.camera.projectionMatrix, this.camera.matrixWorldInverse);
    const frustum = new THREE.Frustum().setFromProjectionMatrix(projection);
    return this.snapshot.objects.filter((object) => {
      const height = object.height_max_m ?? object.canopy_radius_max_m * 2.5;
      const sphere = new THREE.Sphere(
        new THREE.Vector3(object.local_x, height / 2, -object.local_y),
        Math.max(object.canopy_radius_max_m, height / 2),
      );
      return frustum.intersectsSphere(sphere);
    });
  }

  private collectInstances() {
    const instances: PlantInstance[] = [];
    for (const object of this.visibleObjects()) {
      const previous = this.previousLod.get(object.object_id);
      const lod = classifySceneLod(this.projectedDiameter(object), previous);
      this.previousLod.set(object.object_id, lod);
      const prototype = this.assetLibrary.get(object, lod);
      instances.push({ object, prototype, lod, matrix: plantMatrix(object, prototype) });
    }
    return instances;
  }

  private rebuildPlants() {
    disposeInstancedWrappers(this.plantRoot);
    const buckets = new Map<string, InstanceBucket>();
    const instances = this.collectInstances();
    this.currentInstances = instances;
    this.visiblePlantInstances = instances.length;
    for (const instance of instances) {
      instance.prototype.parts.forEach((_part, partIndex) => {
        const key = `${instance.prototype.assetKey}:${instance.lod}:${partIndex}`;
        const bucket = buckets.get(key);
        if (bucket) bucket.items.push(instance);
        else buckets.set(key, { prototype: instance.prototype, partIndex, items: [instance] });
      });
    }

    for (const [key, bucket] of buckets) {
      const part = bucket.prototype.parts[bucket.partIndex];
      for (let offset = 0; offset < bucket.items.length; offset += SCENE_RENDER_TARGETS.maxInstancesPerBatch) {
        const items = bucket.items.slice(offset, offset + SCENE_RENDER_TARGETS.maxInstancesPerBatch);
        const mesh = new THREE.InstancedMesh(part.geometry, part.material, items.length);
        mesh.name = `plants:${key}:${offset}`;
        mesh.userData.objectIds = items.map((item) => item.object.object_id);
        mesh.userData.plantBatch = true;
        mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
        items.forEach((item, index) => mesh.setMatrixAt(index, item.matrix));
        mesh.instanceMatrix.needsUpdate = true;
        mesh.castShadow = part.castShadow && instances.length <= 900;
        mesh.receiveShadow = true;
        mesh.computeBoundingBox();
        mesh.computeBoundingSphere();
        this.plantRoot.add(mesh);
      }
    }
    this.rebuildRoots(instances);
    this.rebuildOutlines(instances);
    this.emitTelemetry();
  }

  private rebuildRoots(instances = this.currentInstances) {
    disposeInstancedWrappers(this.rootsRoot);
    if (!this.rootsVisible) return;
    const withRoots = instances.filter((item) => item.object.root_radius_max_m && item.object.root_radius_max_m > 0);
    if (!withRoots.length) return;
    const geometry = new THREE.RingGeometry(0.66, 1, 48);
    geometry.rotateX(-Math.PI / 2);
    const material = new THREE.MeshBasicMaterial({ color: 0xa76628, transparent: true, opacity: 0.24, depthWrite: false, side: THREE.DoubleSide });
    const mesh = new THREE.InstancedMesh(geometry, material, withRoots.length);
    const matrix = new THREE.Matrix4();
    withRoots.forEach((item, index) => {
      const radius = item.object.root_radius_max_m ?? 0;
      matrix.compose(
        tempVector.set(item.object.local_x, 0.025, -item.object.local_y),
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

  private rebuildOutlines(instances = this.currentInstances) {
    Object.values(this.outlineRoots).forEach(disposeInstancedWrappers);
    const groups: Record<keyof typeof this.outlineRoots, PlantInstance[]> = {
      selected: [], error: [], warning: [], hover: [],
    };
    for (const instance of instances) {
      const id = instance.object.object_id;
      if (this.selectedIds.has(id)) groups.selected.push(instance);
      else if (this.hoveredId === id) groups.hover.push(instance);
      else if (this.issueSeverity.get(id) === 'error') groups.error.push(instance);
      else if (this.issueSeverity.get(id) === 'warning') groups.warning.push(instance);
    }
    (Object.keys(groups) as Array<keyof typeof groups>).forEach((kind) => {
      this.buildOutlineInstances(this.outlineRoots[kind], groups[kind], this.outlineMaterials[kind]);
    });
  }

  private buildOutlineInstances(root: THREE.Group, instances: PlantInstance[], material: THREE.ShaderMaterial) {
    const buckets = new Map<string, { part: PlantPrototype['parts'][number]; items: PlantInstance[] }>();
    for (const instance of instances) {
      instance.prototype.parts.forEach((part, partIndex) => {
        const key = `${instance.prototype.assetKey}:${instance.lod}:${partIndex}`;
        const bucket = buckets.get(key);
        if (bucket) bucket.items.push(instance);
        else buckets.set(key, { part, items: [instance] });
      });
    }
    for (const bucket of buckets.values()) {
      const mesh = new THREE.InstancedMesh(bucket.part.geometry, material, bucket.items.length);
      bucket.items.forEach((item, index) => mesh.setMatrixAt(index, item.matrix));
      mesh.instanceMatrix.needsUpdate = true;
      mesh.frustumCulled = false;
      mesh.renderOrder = 8;
      root.add(mesh);
    }
  }

  private objectAt(event: MouseEvent | PointerEvent) {
    const rect = this.canvas.getBoundingClientRect();
    this.pointer.set(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hit = this.raycaster.intersectObjects(this.plantRoot.children, false)[0];
    if (!hit || hit.instanceId === undefined) return undefined;
    const ids = hit.object.userData.objectIds as string[] | undefined;
    return ids?.[hit.instanceId];
  }

  private handleClick = (event: MouseEvent) => {
    const objectId = this.objectAt(event);
    if (objectId) this.onSelect(objectId);
  };

  private handleDoubleClick = (event: MouseEvent) => {
    const objectId = this.objectAt(event);
    if (!objectId) return;
    this.onSelect(objectId);
    this.selectedIds.clear();
    this.selectedIds.add(objectId);
    this.rebuildOutlines();
    this.focusSelection(true);
  };

  private handlePointerMove = (event: PointerEvent) => {
    const objectId = this.objectAt(event);
    this.onHover?.({ objectId, clientX: event.clientX, clientY: event.clientY });
    if (this.hoveredId === objectId) return;
    this.hoveredId = objectId;
    this.rebuildOutlines();
    this.requestRender(90);
  };

  private handlePointerLeave = (event: PointerEvent) => {
    this.onHover?.({ clientX: event.clientX, clientY: event.clientY });
    if (!this.hoveredId) return;
    this.hoveredId = undefined;
    this.rebuildOutlines();
    this.requestRender(90);
  };

  private handleControlStart = () => this.requestRender(60_000);
  private handleControlChange = () => {
    const now = performance.now();
    if (now - this.lastLodRebuildAt < 120) return;
    this.lastLodRebuildAt = now;
    if (this.lodFrame) return;
    this.lodFrame = requestAnimationFrame(() => {
      this.lodFrame = 0;
      this.rebuildPlants();
    });
  };
  private handleControlEnd = () => {
    this.animationStartedAt = performance.now();
    this.rebuildPlants();
    this.requestRender(420);
  };

  private resize = () => {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    this.renderer.setSize(rect.width, rect.height, false);
    Object.values(this.outlineMaterials).forEach((material) => {
      (material.uniforms.resolution.value as THREE.Vector2).set(rect.width, rect.height);
    });
    this.camera.aspect = rect.width / rect.height;
    this.camera.updateProjectionMatrix();
    this.rebuildPlants();
    this.requestRender(140);
  };

  private requestRender(duration = 120) {
    if (this.disposed) return;
    this.animationStartedAt = Math.max(this.animationStartedAt, performance.now() + duration);
    if (!this.frame) this.frame = requestAnimationFrame(this.render);
  }

  private render = (now: number) => {
    this.frame = 0;
    if (this.disposed) return;
    if (this.cameraAnimation) {
      const elapsed = now - this.cameraAnimation.startedAt;
      const progress = Math.min(1, elapsed / this.cameraAnimation.duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      this.camera.position.lerpVectors(this.cameraAnimation.fromPosition, this.cameraAnimation.toPosition, eased);
      this.controls.target.lerpVectors(this.cameraAnimation.fromTarget, this.cameraAnimation.toTarget, eased);
      if (progress >= 1) {
        this.cameraAnimation = undefined;
        this.rebuildPlants();
      }
    }
    this.controls.update();
    this.renderer.info.reset();
    this.renderer.render(this.scene, this.camera);
    this.recordFrame(now);
    if (this.cameraAnimation || now < this.animationStartedAt) this.frame = requestAnimationFrame(this.render);
  };

  private recordFrame(now: number) {
    const duration = now - this.lastFrameAt;
    this.lastFrameAt = now;
    if (duration > 0 && duration < 250) {
      this.frameSamples.push(duration);
      if (this.frameSamples.length > 90) this.frameSamples.shift();
    }
    if (now - this.telemetryFrame > 500) {
      this.telemetryFrame = now;
      this.emitTelemetry();
    }
  }

  private emitTelemetry() {
    if (!this.onTelemetry) return;
    const averageDuration = this.frameSamples.length
      ? this.frameSamples.reduce((sum, duration) => sum + duration, 0) / this.frameSamples.length
      : 0;
    this.onTelemetry({
      rendererGeneration: this.rendererGeneration,
      // Zero means that the sampler is warming up. Never report the target as
      // though it had been measured before a real frame interval exists.
      fps: averageDuration > 0 ? Math.min(SCENE_RENDER_TARGETS.targetFps, Math.round(1000 / averageDuration)) : 0,
      drawCalls: this.renderer.info.render.calls,
      triangles: this.renderer.info.render.triangles,
      plantInstances: this.snapshot?.objects.length ?? 0,
      visiblePlantInstances: this.visiblePlantInstances,
    });
  }

  loadedModelCount() {
    return this.assetLibrary.loadedModelCount();
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    if (this.frame) cancelAnimationFrame(this.frame);
    if (this.lodFrame) cancelAnimationFrame(this.lodFrame);
    this.resizeObserver.disconnect();
    this.canvas.removeEventListener('click', this.handleClick);
    this.canvas.removeEventListener('dblclick', this.handleDoubleClick);
    this.canvas.removeEventListener('pointermove', this.handlePointerMove);
    this.canvas.removeEventListener('pointerleave', this.handlePointerLeave);
    this.controls.removeEventListener('start', this.handleControlStart);
    this.controls.removeEventListener('change', this.handleControlChange);
    this.controls.removeEventListener('end', this.handleControlEnd);
    this.controls.dispose();
    Object.values(this.outlineRoots).forEach(disposeInstancedWrappers);
    Object.values(this.outlineMaterials).forEach((material) => material.dispose());
    disposeObjectTree(this.scene);
    this.assetLibrary.dispose();
    this.renderer.dispose();
  }
}

export type { SceneCameraMode, SceneHover };
