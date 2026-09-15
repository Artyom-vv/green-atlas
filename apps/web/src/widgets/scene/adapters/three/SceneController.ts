import type {
  PlantingZoneAssignment,
  ScenePlantObject,
  SceneSnapshot,
  ValidationIssue,
} from '@green/api-client';
import * as THREE from 'three';
import { MapControls } from 'three/examples/jsm/controls/MapControls.js';
import { CSM } from 'three/examples/jsm/csm/CSM.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { OutlinePass } from 'three/examples/jsm/postprocessing/OutlinePass.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import {
  PlantAssetLibrary,
  type PlantPrototype,
} from '@/widgets/scene/adapters/three/plantAssets';
import {
  buildContextScene,
  disposeObjectTree,
  type SceneContextLike,
} from '@/widgets/scene/adapters/three/sceneGeometry';
import {
  classifySceneLod,
  nextSceneQualityState,
  sceneDrawingBufferSize,
  sceneFrameWindow,
  sceneOutlineBufferWidth,
  SCENE_RENDER_TARGETS,
  type SceneLod,
  type SceneQualityState,
  type SceneRenderTelemetry,
} from '@/widgets/scene/model/sceneRenderContract';
import {
  sourceCenterToWorld,
  validPlanViewState,
  worldCenterToSource,
  writePlanViewStateAttributes,
  type PlanViewState,
} from '@/entities/editor/model/planViewState';

type SceneSnapshotWithContext = SceneSnapshot & {
  context_features?: SceneContextLike[];
};

type SceneCameraMode = 'overview' | 'ground';
type SceneCameraState = SceneCameraMode | 'custom';
type SceneHover = { objectId?: string; clientX: number; clientY: number };

export type SceneControllerOptions = {
  canvas: HTMLCanvasElement;
  assetLibrary: PlantAssetLibrary;
  renderer?: THREE.WebGLRenderer;
  onSelect: (objectId: string) => void;
  onMoveTarget?: (coordinate: [number, number]) => void;
  onHover?: (hover: SceneHover) => void;
  onTelemetry?: (telemetry: SceneRenderTelemetry) => void;
  initialViewState?: PlanViewState;
  onViewStateChange?: (state: PlanViewState) => void;
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
const groundEyeHeightM = 1.7;
const minimumPlantDiameterPx = 1.25;
const tempVector = new THREE.Vector3();
const tempScale = new THREE.Vector3();
const tempQuaternion = new THREE.Quaternion();
let rendererGenerationSequence = 0;

export function createSceneRenderer(canvas: HTMLCanvasElement) {
  try {
    return new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: false,
      powerPreference: 'high-performance',
      preserveDrawingBuffer: false,
    });
  } catch (primaryError) {
    // Integrated GPUs and embedded browsers can temporarily refuse a
    // high-performance/antialiased context (especially after a context was
    // reclaimed). A plain WebGL context is still fully capable of rendering
    // the scene and is preferable to incorrectly declaring 3D unsupported.
    try {
      return new THREE.WebGLRenderer({
        canvas,
        antialias: false,
        alpha: false,
        powerPreference: 'default',
        preserveDrawingBuffer: false,
      });
    } catch (fallbackError) {
      throw new AggregateError(
        [primaryError, fallbackError],
        'Unable to create a WebGL renderer',
      );
    }
  }
}

function deterministicAngle(id: string) {
  let hash = 2166136261;
  for (let index = 0; index < id.length; index += 1) {
    hash ^= id.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return ((hash >>> 0) / 0xffffffff) * Math.PI * 2;
}

function plantMatrix(
  object: ScenePlantObject,
  prototype: PlantPrototype,
  elevation = 0,
) {
  const targetRadius = Math.max(0.18, object.canopy_radius_max_m);
  const targetHeight = Math.max(
    object.kind === 'tree' ? 1.8 : 0.35,
    object.height_max_m ??
      (object.kind === 'tree' ? targetRadius * 2.55 : targetRadius * 1.25),
  );
  tempScale.set(
    targetRadius / Math.max(0.01, prototype.nominalRadiusM),
    targetHeight / Math.max(0.01, prototype.nominalHeightM),
    targetRadius / Math.max(0.01, prototype.nominalRadiusM),
  );
  tempQuaternion.setFromAxisAngle(
    THREE.Object3D.DEFAULT_UP,
    deterministicAngle(object.object_id),
  );
  return new THREE.Matrix4().compose(
    tempVector.set(object.local_x, elevation, -object.local_y),
    tempQuaternion,
    tempScale,
  );
}

function severityByObject(issues: ValidationIssue[]) {
  const result = new Map<string, ValidationIssue['severity']>();
  for (const issue of issues) {
    if (!issue.object_id) continue;
    const previous = result.get(issue.object_id);
    if (!previous || issue.severity === 'error')
      result.set(issue.object_id, issue.severity);
  }
  return result;
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
      varying vec3 pickColor;
      varying vec2 plantUv;
      void main() {
        pickColor = instanceColor;
        plantUv = uv;
        gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      varying vec3 pickColor;
      varying vec2 plantUv;
      uniform sampler2D plantMap;
      uniform sampler2D plantAlphaMap;
      uniform float plantAlphaTest;
      void main() {
        float alpha = texture2D(plantMap, plantUv).a * texture2D(plantAlphaMap, plantUv).g;
        if (alpha < plantAlphaTest) discard;
        gl_FragColor = vec4(pickColor, 1.0);
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

export class SceneController {
  private readonly canvas: HTMLCanvasElement;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly composer: EffectComposer;
  private readonly csmMaterials = new WeakSet<THREE.Material>();
  private readonly scene = new THREE.Scene();
  private readonly pickScene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(42, 1, 0.08, 20_000);
  private readonly controls: MapControls;
  private readonly plantRoot = new THREE.Group();
  private readonly pickRoot = new THREE.Group();
  private readonly contextRoot = new THREE.Group();
  private readonly workZonesRoot = new THREE.Group();
  private readonly rootsRoot = new THREE.Group();
  private readonly outlineRoots = {
    selected: new THREE.Group(),
    error: new THREE.Group(),
    warning: new THREE.Group(),
    hover: new THREE.Group(),
  };
  private readonly outlineProxyMaterial = new THREE.MeshBasicMaterial({
    color: 0xffffff,
    colorWrite: false,
    depthWrite: false,
    toneMapped: false,
  });
  // Until the alpha-aware ID buffer lands, use the honest fallback promised
  // by the interaction contract: one rectangular model volume. A synthetic
  // crown made of spheres looked like a contour but did not match the asset.
  private readonly outlinePass: OutlinePass;
  private readonly outputPass: OutputPass;
  private readonly rootGeometry = new THREE.RingGeometry(0.66, 1, 48).rotateX(
    -Math.PI / 2,
  );
  private readonly pickTarget = new THREE.WebGLRenderTarget(1, 1, {
    depthBuffer: true,
    stencilBuffer: false,
  });
  private readonly pickPixel = new Uint8Array(4);
  private readonly groundRaycaster = new THREE.Raycaster();
  private readonly groundPointer = new THREE.Vector2();
  private readonly pickIds: string[] = [];
  private readonly whiteAlphaTexture = new THREE.DataTexture(
    new Uint8Array([255, 255, 255, 255]),
    1,
    1,
  );
  private readonly rootMaterial = new THREE.MeshBasicMaterial({
    color: 0xa76628,
    transparent: true,
    opacity: 0.24,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  private readonly objectById = new Map<string, ScenePlantObject>();
  private readonly previousLod = new Map<string, SceneLod>();
  private currentInstances: PlantInstance[] = [];
  private readonly selectedIds = new Set<string>();
  private readonly issueSeverity = new Map<
    string,
    ValidationIssue['severity']
  >();
  private readonly frameSamples: number[] = [];
  private readonly resizeObserver: ResizeObserver;
  private readonly onSelect: (objectId: string) => void;
  private readonly onMoveTarget?: (coordinate: [number, number]) => void;
  private readonly onHover?: (hover: SceneHover) => void;
  private readonly onTelemetry?: (telemetry: SceneRenderTelemetry) => void;
  private readonly onViewStateChange?: (state: PlanViewState) => void;
  private assetLibrary: PlantAssetLibrary;
  private assetUnsubscribe?: () => void;
  private snapshot?: SceneSnapshotWithContext;
  private rootsVisible = false;
  private hoveredId?: string;
  private frame = 0;
  private rendering = false;
  private sampleNextFrame = false;
  private pointerFrame = 0;
  private hoverTimer?: ReturnType<typeof setTimeout>;
  private pendingPointer?: { clientX: number; clientY: number };
  private interacting = false;
  private cameraState: SceneCameraState = 'overview';
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
  private lastFrameAt = 0;
  private readonly rendererGeneration = ++rendererGenerationSequence;
  private visiblePlantInstances = 0;
  private qualityState: SceneQualityState = {
    renderScale: 1.25,
    lastChangedAt: 0,
  };
  private effectivePixelRatio = 1;
  private environmentTexture?: THREE.Texture;
  private csm?: CSM;
  private disposed = false;
  private pendingPlanViewState?: PlanViewState;
  private terrainElevationAt: (x: number, z: number) => number = () => 0;
  private relocateSelection = false;
  private readonly navigationBounds = new THREE.Box3();
  // Billboard matrices are refreshed while the camera moves. Reuse scratch
  // objects here: allocating four Three.js objects per plant, per pass, per
  // frame quickly creates enough GC pressure to lose the WebGL context.
  private readonly billboardPosition = new THREE.Vector3();
  private readonly billboardScale = new THREE.Vector3();
  private readonly billboardSourceQuaternion = new THREE.Quaternion();
  private readonly billboardMatrix = new THREE.Matrix4();

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

  private refreshBillboards(root: THREE.Group) {
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

  constructor(options: SceneControllerOptions) {
    this.canvas = options.canvas;
    this.assetLibrary = options.assetLibrary;
    this.onSelect = options.onSelect;
    this.onMoveTarget = options.onMoveTarget;
    this.onHover = options.onHover;
    this.onTelemetry = options.onTelemetry;
    this.onViewStateChange = options.onViewStateChange;
    this.pendingPlanViewState = options.initialViewState;
    this.assetUnsubscribe = this.assetLibrary.subscribe(() => {
      if (this.disposed) return;
      this.rebuildPlants();
      this.requestRender(220);
    });

    this.renderer = options.renderer ?? createSceneRenderer(this.canvas);
    this.renderer.setPixelRatio(
      Math.min(
        window.devicePixelRatio,
        SCENE_RENDER_TARGETS.maxDevicePixelRatio,
      ),
    );
    this.renderer.setClearColor(0xd8dfdc, 1);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.92;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    // EffectComposer performs extra scene renders for post-processing. Keep
    // the cascaded shadow atlas explicit so those renders do not rebuild it.
    this.renderer.shadowMap.autoUpdate = false;
    // Reset explicitly once per frame so diagnostics remain comparable when
    // outline batches appear or disappear.
    this.renderer.info.autoReset = false;

    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    // OutlinePass owns several full/half/quarter resolution targets and
    // performs another depth render. One shared pass keeps the scene below
    // the GPU-memory budget; the active semantic class is selected below.
    this.outlinePass = this.createOutlinePass(selectedColor, 5.2);
    this.configureAlphaAwareOutlineMask();
    this.composer.addPass(this.outlinePass);
    this.outputPass = new OutputPass();
    this.composer.addPass(this.outputPass);

    this.scene.background = new THREE.Color(0xf2f5f7);
    // Atmospheric depth should separate planes, not erase a city-scale plan.
    // A denser value made the truthful DXF context and young plants disappear
    // in the default overview on 700–1000 m scenes.
    this.scene.fog = new THREE.FogExp2(0xf2f5f7, 0.000045);
    this.camera.position.set(36, 31, 42);

    // MapControls matches an editing workspace: primary drag pans the site,
    // secondary drag rotates, and the wheel changes scale around a stable
    // target. OrbitControls made accidental rotations far too easy.
    this.controls = new MapControls(this.camera, this.canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.055;
    this.controls.rotateSpeed = 0.72;
    this.controls.panSpeed = 0.82;
    this.controls.zoomSpeed = 0.9;
    // Keep a meaningful downward component so the finite DXF surface cannot
    // collapse to a one-pixel horizon and disappear during rotation.
    this.controls.maxPolarAngle = Math.PI / 2.1;
    this.controls.minDistance = 1.2;
    // Keep navigation anchored to the horizontal site plane. Screen-space
    // panning can lift the orbit target hundreds of metres into the sky, and
    // zoom-to-cursor can then dolly the whole finite DXF base out of view.
    this.controls.screenSpacePanning = false;
    this.controls.zoomToCursor = false;
    this.controls.target.set(0, 0, 0);
    this.controls.update();

    this.plantRoot.name = 'plant-instances';
    this.pickRoot.name = 'plant-hit-volumes';
    this.pickScene.add(this.pickRoot);
    this.contextRoot.name = 'context-root';
    this.rootsRoot.name = 'root-zones';
    Object.values(this.outlineRoots).forEach((root) => {
      root.name = 'outline-proxies';
      this.scene.add(root);
    });
    this.scene.add(
      this.contextRoot,
      this.workZonesRoot,
      this.plantRoot,
      this.rootsRoot,
    );
    this.addEnvironment();

    this.canvas.addEventListener('click', this.handleClick);
    this.canvas.addEventListener('dblclick', this.handleDoubleClick);
    this.canvas.addEventListener('pointermove', this.handlePointerMove, {
      passive: true,
    });
    this.canvas.addEventListener('pointerleave', this.handlePointerLeave, {
      passive: true,
    });
    this.controls.addEventListener('start', this.handleControlStart);
    this.controls.addEventListener('change', this.handleControlChange);
    this.controls.addEventListener('end', this.handleControlEnd);
    this.resizeObserver = new ResizeObserver(this.resize);
    this.resizeObserver.observe(this.canvas);
    this.resize();
    this.requestRender(280);
  }

  private createOutlinePass(color: THREE.Color, strength: number) {
    const pass = new OutlinePass(
      new THREE.Vector2(1, 1),
      this.scene,
      this.camera,
      [],
    );
    pass.edgeStrength = strength;
    pass.edgeGlow = 0;
    pass.edgeThickness = 3;
    pass.pulsePeriod = 0;
    pass.visibleEdgeColor.copy(color);
    pass.hiddenEdgeColor.copy(color).multiplyScalar(0.42);
    return pass;
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

  private addEnvironment() {
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    const room = new RoomEnvironment();
    this.environmentTexture = pmrem.fromScene(room, 0.04).texture;
    room.dispose();
    pmrem.dispose();
    this.scene.environment = this.environmentTexture;
    this.scene.environmentIntensity = 0.45;

    this.scene.add(new THREE.HemisphereLight(0xfffaf0, 0x4d5a50, 0.75));
    this.csm = new CSM({
      camera: this.camera,
      parent: this.scene,
      cascades: 2,
      maxFar: 280,
      mode: 'practical',
      shadowMapSize: 1_024,
      shadowBias: 0.00012,
      lightDirection: new THREE.Vector3(-0.58, -1, 0.42).normalize(),
      lightIntensity: 1.35,
      lightNear: 0.4,
      lightFar: 640,
      lightMargin: 80,
    });
    this.csm.fade = true;
  }

  private configureCsmMaterials(root: THREE.Object3D) {
    const csm = this.csm;
    if (!csm) return;
    root.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return;
      const materials = Array.isArray(object.material)
        ? object.material
        : [object.material];
      for (const material of materials) {
        if (this.csmMaterials.has(material)) continue;
        if (!(
          material instanceof THREE.MeshStandardMaterial ||
          material instanceof THREE.MeshPhysicalMaterial
        ))
          continue;
        csm.setupMaterial(material);
        this.csmMaterials.add(material);
      }
    });
  }

  /**
   * CSM keeps a strong shader-map reference to every configured material and
   * replaces its compile hook. Context meshes are rebuilt when a project is
   * refreshed, so detach those references before disposing their materials.
   */
  private detachCsmMaterials(root: THREE.Object3D) {
    const csm = this.csm;
    if (!csm) return;
    root.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return;
      const materials = Array.isArray(object.material)
        ? object.material
        : [object.material];
      for (const material of materials) {
        if (!this.csmMaterials.delete(material)) continue;
        delete (material as unknown as { onBeforeCompile?: unknown })
          .onBeforeCompile;
        if (material.defines) {
          delete material.defines.USE_CSM;
          delete material.defines.CSM_CASCADES;
          delete material.defines.CSM_FADE;
        }
        csm.shaders.delete(material);
        material.needsUpdate = true;
      }
    });
  }

  private disposeContext() {
    for (const child of [...this.contextRoot.children]) {
      this.contextRoot.remove(child);
      this.detachCsmMaterials(child);
      disposeObjectTree(child);
    }
  }

  updateSnapshot(
    snapshot: SceneSnapshotWithContext,
    issues: ValidationIssue[] = [],
  ) {
    const firstSnapshot = !this.snapshot;
    const firstPopulatedSnapshot =
      !this.snapshot?.objects.length && snapshot.objects.length > 0;
    const horizonChanged =
      this.snapshot?.horizon_year !== snapshot.horizon_year;
    this.snapshot = snapshot;
    this.objectById.clear();
    snapshot.objects.forEach((object) =>
      this.objectById.set(object.object_id, object),
    );
    this.issueSeverity.clear();
    severityByObject(issues).forEach((severity, id) =>
      this.issueSeverity.set(id, severity),
    );

    this.disposeContext();
    const context = buildContextScene(
      snapshot.context_features ?? [],
      snapshot.vertical_primitives ?? [],
    );
    this.terrainElevationAt = context.terrainElevationAt;
    this.contextRoot.add(context.group);
    this.contextRoot.updateWorldMatrix(true, true);
    this.navigationBounds
      .setFromObject(this.contextRoot)
      .union(this.boundsForObjects(snapshot.objects));
    this.configureCsmMaterials(this.contextRoot);
    this.rebuildPlants();
    if (this.pendingPlanViewState && this.applyPendingPlanViewState()) {
      // The incoming 2D viewport is authoritative on first render.
    } else if (firstSnapshot || firstPopulatedSnapshot)
      this.fitPrimaryPlanting(false);
    else if (horizonChanged && this.cameraState === 'overview')
      this.fitPrimaryPlanting(true);
    else if (horizonChanged && this.cameraState === 'ground')
      this.focusSelection(true);
    this.requestRender(420);
  }

  updateIssues(issues: ValidationIssue[]) {
    this.issueSeverity.clear();
    severityByObject(issues).forEach((severity, id) =>
      this.issueSeverity.set(id, severity),
    );
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
    this.cameraState = mode;
    if (mode === 'overview') this.fitAll(true);
    else this.focusSelection(true);
  }

  fitPlantings() {
    this.cameraState = 'overview';
    if (this.snapshot)
      this.fitBounds(this.boundsForObjects(this.snapshot.objects), true);
  }

  zoom(factor: number) {
    const target = this.controls.target.clone();
    const offset = this.camera.position
      .clone()
      .sub(target)
      .multiplyScalar(factor);
    offset.clampLength(this.controls.minDistance, this.controls.maxDistance);
    this.moveCamera(target.clone().add(offset), target, 180);
  }

  fitExtent(extent: readonly number[]) {
    if (!this.snapshot || extent.length !== 4 || !extent.every(Number.isFinite))
      return;
    const origin = this.snapshot.coordinate_origin ?? [0, 0];
    const bounds = new THREE.Box3();
    for (const x of [extent[0], extent[2]])
      for (const y of [extent[1], extent[3]]) {
        const [wx, wz] = sourceCenterToWorld([x, y], origin);
        bounds.expandByPoint(
          new THREE.Vector3(wx, this.terrainElevationAt(wx, wz), wz),
        );
      }
    this.fitBounds(bounds, true);
  }

  updateWorkZones(zones: PlantingZoneAssignment[], selectedIds: string[]) {
    for (const child of [...this.workZonesRoot.children]) {
      this.workZonesRoot.remove(child);
      disposeObjectTree(child);
    }
    if (!this.snapshot) return;
    const origin = this.snapshot.coordinate_origin ?? [0, 0];
    for (const zone of zones) {
      const positions: number[] = [];
      const visit = (value: unknown) => {
        if (!Array.isArray(value)) return;
        if (Array.isArray(value[0]) && typeof value[0][0] === 'number') {
          const points = value as number[][];
          for (let i = 1; i < points.length; i++) {
            const a = points[i - 1],
              b = points[i];
            const steps = Math.min(
              512,
              Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / 5)),
            );
            for (let step = 0; step < steps; step++)
              for (const t of [step / steps, (step + 1) / steps]) {
                const [x, z] = sourceCenterToWorld(
                  [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t],
                  origin,
                );
                positions.push(x, this.terrainElevationAt(x, z) + 0.18, z);
              }
          }
        } else value.forEach(visit);
      };
      visit(zone.geometry.coordinates);
      if (!positions.length) continue;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute(
        'position',
        new THREE.Float32BufferAttribute(positions, 3),
      );
      const lines = new THREE.LineSegments(
        geometry,
        new THREE.LineBasicMaterial({
          color: zone.id && selectedIds.includes(zone.id) ? 0x225cff : 0x718878,
          depthWrite: false,
        }),
      );
      lines.name = `work-zone:${zone.id}`;
      this.workZonesRoot.add(lines);
    }
    this.requestRender(180);
  }

  fitSelection() {
    const selected = [...this.selectedIds].flatMap((id) => {
      const object = this.objectById.get(id);
      return object ? [object] : [];
    });
    if (!selected.length) {
      this.fitAll(true);
      return;
    }
    this.cameraState = 'overview';
    this.fitBounds(this.boundsForObjects(selected), true);
  }

  importPlanViewState(state: PlanViewState) {
    if (!validPlanViewState(state)) return;
    this.pendingPlanViewState = state;
    this.applyPendingPlanViewState();
  }

  private publishPlanViewState() {
    const state = this.exportPlanViewState();
    if (!state) return;
    writePlanViewStateAttributes(this.canvas, state);
    this.onViewStateChange?.(state);
  }

  exportPlanViewState(): PlanViewState | undefined {
    if (!this.snapshot) return undefined;
    const rect = this.canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return undefined;
    const points = [
      this.groundPointAtNdc(-1, 1),
      this.groundPointAtNdc(1, 1),
      this.groundPointAtNdc(-1, -1),
      this.groundPointAtNdc(1, -1),
    ];
    const target = this.controls.target;
    const cameraOffset = this.camera.position.clone().sub(target);
    const sourceCenter = worldCenterToSource(
      target.x,
      target.z,
      this.snapshot.coordinate_origin ?? [0, 0],
    );
    let resolution: number;
    if (points.every(Boolean)) {
      const [topLeft, topRight, bottomLeft, bottomRight] =
        points as THREE.Vector3[];
      const widthM =
        (topLeft.distanceTo(topRight) + bottomLeft.distanceTo(bottomRight)) / 2;
      const heightM =
        (topLeft.distanceTo(bottomLeft) + topRight.distanceTo(bottomRight)) / 2;
      resolution = (widthM / rect.width + heightM / rect.height) / 2;
    } else {
      const distance = Math.max(0.1, cameraOffset.length());
      const downward = Math.max(0.08, Math.abs(cameraOffset.y) / distance);
      resolution =
        (2 *
          distance *
          Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2)) /
        (rect.height * downward);
    }
    return {
      center: sourceCenter,
      resolution,
      rotation: Math.atan2(cameraOffset.x, cameraOffset.z),
      viewport: [rect.width, rect.height],
    };
  }

  private groundPointAtNdc(x: number, y: number) {
    // Camera transfers can be measured before the next render. Unproject
    // must use the new pose, not matrixWorld from the previous frame.
    this.camera.updateMatrixWorld(true);
    const point = new THREE.Vector3(x, y, 0.5).unproject(this.camera);
    const direction = point.sub(this.camera.position).normalize();
    if (direction.y >= -1e-5) return undefined;
    return this.camera.position
      .clone()
      .addScaledVector(
        direction,
        (this.controls.target.y - this.camera.position.y) / direction.y,
      );
  }

  private applyPendingPlanViewState() {
    const state = this.pendingPlanViewState;
    const snapshot = this.snapshot;
    const rect = this.canvas.getBoundingClientRect();
    if (!state || !snapshot || rect.width <= 0 || rect.height <= 0)
      return false;
    const [worldX, worldZ] = sourceCenterToWorld(
      state.center,
      snapshot.coordinate_origin ?? [0, 0],
    );
    const target = new THREE.Vector3(
      worldX,
      this.terrainElevationAt(worldX, worldZ),
      worldZ,
    );
    const pitch = THREE.MathUtils.degToRad(58);
    const groundHeight = state.resolution * rect.height;
    const distance = Math.max(
      2,
      (groundHeight * Math.sin(pitch)) /
        (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2)),
    );
    const horizontal = Math.cos(pitch) * distance;
    const position = new THREE.Vector3(
      target.x + Math.sin(state.rotation) * horizontal,
      target.y + Math.sin(pitch) * distance,
      target.z + Math.cos(state.rotation) * horizontal,
    );
    this.camera.up.set(0, 1, 0);
    this.camera.near = THREE.MathUtils.clamp(distance / 2000, 0.04, 1);
    this.camera.far = Math.max(120, distance * 8);
    this.camera.updateProjectionMatrix();
    this.controls.maxDistance = Math.max(60, distance * 4);
    this.cameraState = 'custom';
    this.pendingPlanViewState = undefined;
    this.moveCamera(position, target, 0);
    // Perspective projection over an oblique plane forms a trapezoid. Match
    // the effective CSS-pixel scale measured from its real ground footprint.
    const measured = this.exportPlanViewState();
    if (measured?.resolution) {
      const ratio = state.resolution / measured.resolution;
      this.camera.position.sub(target).multiplyScalar(ratio).add(target);
      this.controls.update();
    }
    this.publishPlanViewState();
    return true;
  }

  private boundsForObjects(objects: readonly ScenePlantObject[]) {
    const bounds = new THREE.Box3();
    for (const object of objects) {
      const radius = Math.max(0.4, object.canopy_radius_max_m);
      const terrainY = this.terrainElevationAt(object.local_x, -object.local_y);
      bounds.expandByPoint(
        new THREE.Vector3(
          object.local_x - radius,
          terrainY,
          -object.local_y - radius,
        ),
      );
      bounds.expandByPoint(
        new THREE.Vector3(
          object.local_x + radius,
          terrainY + (object.height_max_m ?? radius * 2.5),
          -object.local_y + radius,
        ),
      );
    }
    return bounds;
  }

  private fitPrimaryPlanting(animate = true) {
    if (!this.snapshot?.objects.length) {
      this.fitAll(animate);
      return;
    }
    this.fitBounds(this.boundsForObjects(this.snapshot.objects), animate);
  }

  fitAll(animate = true) {
    if (!this.snapshot) return;
    const bounds = this.boundsForObjects(this.snapshot.objects);
    // When plants exist, the useful overview is the planting operation with
    // surrounding DXF context—not the entire imported sheet. The latter can
    // be substantially larger and used to reduce every young tree to a pixel.
    this.contextRoot.updateWorldMatrix(true, true);
    bounds.union(new THREE.Box3().setFromObject(this.contextRoot));
    this.fitBounds(bounds, animate);
  }

  private fitBounds(bounds: THREE.Box3, animate: boolean) {
    if (bounds.isEmpty())
      bounds.setFromCenterAndSize(
        new THREE.Vector3(),
        new THREE.Vector3(40, 5, 40),
      );
    const fitted = bounds.clone();
    const unpaddedSize = fitted.getSize(new THREE.Vector3());
    const horizontalPadding = Math.max(
      3,
      Math.max(unpaddedSize.x, unpaddedSize.z) * 0.08,
    );
    const verticalPadding = Math.max(0.75, unpaddedSize.y * 0.08);
    fitted.min.add(
      new THREE.Vector3(-horizontalPadding, -0.2, -horizontalPadding),
    );
    fitted.max.add(
      new THREE.Vector3(horizontalPadding, verticalPadding, horizontalPadding),
    );

    const center = fitted.getCenter(new THREE.Vector3());
    const size = fitted.getSize(new THREE.Vector3());
    const span = Math.max(12, size.x, size.z, size.y * 2);
    // Fit around the actual vertical centre. Targeting a point near ground
    // level framed the trunk correctly but pushed a mature crown entirely
    // above the viewport, making a valid full-height model look empty.
    const target = center.clone();
    const cameraOffset = new THREE.Vector3(0.58, 0.52, 0.7).normalize();
    const right = new THREE.Vector3()
      .crossVectors(this.camera.up, cameraOffset)
      .normalize();
    const viewUp = new THREE.Vector3()
      .crossVectors(cameraOffset, right)
      .normalize();
    const tanVertical = Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2);
    const tanHorizontal = tanVertical * Math.max(0.1, this.camera.aspect);
    let distance = 12;
    for (const x of [fitted.min.x, fitted.max.x]) {
      for (const y of [fitted.min.y, fitted.max.y]) {
        for (const z of [fitted.min.z, fitted.max.z]) {
          const relative = new THREE.Vector3(x, y, z).sub(target);
          const depthTowardCamera = relative.dot(cameraOffset);
          distance = Math.max(
            distance,
            depthTowardCamera + Math.abs(relative.dot(right)) / tanHorizontal,
            depthTowardCamera + Math.abs(relative.dot(viewUp)) / tanVertical,
          );
        }
      }
    }
    distance *= 1.06;
    const position = target.clone().addScaledVector(cameraOffset, distance);
    this.controls.maxDistance = Math.max(60, span * 3.5);
    this.camera.near = THREE.MathUtils.clamp(span / 700, 0.06, 1.2);
    this.camera.far = Math.max(120, this.controls.maxDistance + span * 1.5);
    this.camera.updateProjectionMatrix();
    if (this.csm) {
      this.csm.maxFar = Math.min(
        this.camera.far,
        THREE.MathUtils.clamp(span * 2.2, 100, 720),
      );
      this.csm.updateFrustums();
    }
    this.moveCamera(position, target, animate ? 680 : 0);
  }

  focusSelection(groundLevel = false) {
    const selected = [...this.selectedIds]
      .map((id) => this.objectById.get(id))
      .find(Boolean);
    if (!selected) {
      this.fitAll(true);
      return;
    }
    const height = Math.max(
      2,
      selected.height_max_m ?? selected.canopy_radius_max_m * 2.5,
    );
    const radius = Math.max(0.6, selected.canopy_radius_max_m);
    const terrainY = this.terrainElevationAt(
      selected.local_x,
      -selected.local_y,
    );
    const target = new THREE.Vector3(
      selected.local_x,
      terrainY + (groundLevel ? Math.min(1.45, height * 0.18) : height * 0.45),
      -selected.local_y,
    );
    const heading = new THREE.Vector3(
      0.82,
      groundLevel ? 0 : 0.58,
      1,
    ).normalize();
    const distance = groundLevel
      ? Math.max(radius * 4.5, height * 1.5, 7)
      : Math.max(radius * 4.2, height * 1.25, 7);
    const position = target.clone().addScaledVector(heading, distance);
    if (groundLevel) position.y = terrainY + groundEyeHeightM;
    else position.y = Math.max(terrainY + groundEyeHeightM, position.y);
    this.camera.near = groundLevel ? 0.04 : 0.06;
    this.camera.far = Math.max(120, distance * 10);
    this.camera.updateProjectionMatrix();
    if (this.csm) {
      this.csm.maxFar = Math.min(
        this.camera.far,
        THREE.MathUtils.clamp(distance * 5, 60, 280),
      );
      this.csm.updateFrustums();
    }
    this.moveCamera(position, target, 620);
  }

  setRelocateSelection(enabled: boolean) {
    this.relocateSelection =
      enabled && this.selectedIds.size > 0 && Boolean(this.onMoveTarget);
    this.canvas.style.cursor = this.relocateSelection ? 'crosshair' : '';
  }

  private moveCamera(
    position: THREE.Vector3,
    target: THREE.Vector3,
    duration: number,
  ) {
    if (!duration) {
      this.cameraAnimation = undefined;
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
    const terrainY = this.terrainElevationAt(object.local_x, -object.local_y);
    const position = tempVector.set(
      object.local_x,
      terrainY + (object.height_max_m ?? 2) / 2,
      -object.local_y,
    );
    const distance = Math.max(0.1, this.camera.position.distanceTo(position));
    const visibleHeight =
      2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2) * distance;
    return (
      ((object.canopy_radius_max_m * 2) / visibleHeight) *
      Math.max(1, rect.height)
    );
  }

  private visibleObjects() {
    if (!this.snapshot) return [];
    this.camera.updateMatrixWorld();
    const projection = new THREE.Matrix4().multiplyMatrices(
      this.camera.projectionMatrix,
      this.camera.matrixWorldInverse,
    );
    const frustum = new THREE.Frustum().setFromProjectionMatrix(projection);
    return this.snapshot.objects.filter((object) => {
      // A selected planting is an explicit editing target. Keep it resident
      // through camera transitions even if a one-frame frustum is stale.
      if (this.selectedIds.has(object.object_id)) return true;
      const height = object.height_max_m ?? object.canopy_radius_max_m * 2.5;
      const terrainY = this.terrainElevationAt(object.local_x, -object.local_y);
      const sphere = new THREE.Sphere(
        new THREE.Vector3(
          object.local_x,
          terrainY + height / 2,
          -object.local_y,
        ),
        Math.max(object.canopy_radius_max_m, height / 2),
      );
      return frustum.intersectsSphere(sphere);
    });
  }

  private collectInstances() {
    const instances: PlantInstance[] = [];
    for (const object of this.visibleObjects()) {
      const diameterPx = this.projectedDiameter(object);
      if (
        diameterPx < minimumPlantDiameterPx &&
        !this.selectedIds.has(object.object_id) &&
        this.hoveredId !== object.object_id
      )
        continue;
      const previous = this.previousLod.get(object.object_id);
      const lod = classifySceneLod(diameterPx, previous);
      this.previousLod.set(object.object_id, lod);
      const prototype = this.assetLibrary.get(object, lod);
      instances.push({
        object,
        prototype,
        lod,
        matrix: plantMatrix(
          object,
          prototype,
          this.terrainElevationAt(object.local_x, -object.local_y),
        ),
      });
    }
    return instances;
  }

  private rebuildPlants() {
    disposeInstancedWrappers(this.plantRoot);
    disposeInstancedWrappers(this.pickRoot);
    const buckets = new Map<string, InstanceBucket>();
    const instances = this.collectInstances();
    this.pickIds.length = 0;
    this.pickIds.push(...instances.map((item) => item.object.object_id));
    const pickIndex = new Map(this.pickIds.map((id, index) => [id, index + 1]));
    this.currentInstances = instances;
    this.visiblePlantInstances = instances.length;
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
        mesh.receiveShadow = true;
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
    this.rebuildRoots(instances);
    this.rebuildOutlines(instances);
    this.configureCsmMaterials(this.plantRoot);
    // Renderer counters are authoritative only after the requested frame.
    // Force that rendered frame to publish instead of briefly replacing live
    // draw-call/triangle telemetry with zeros during a rebuild.
    this.telemetryFrame = 0;
  }

  private rebuildRoots(instances = this.currentInstances) {
    disposeInstancedWrappers(this.rootsRoot);
    if (!this.rootsVisible) return;
    const withRoots = instances.filter(
      (item) =>
        item.object.root_radius_max_m && item.object.root_radius_max_m > 0,
    );
    if (!withRoots.length) return;
    const mesh = new THREE.InstancedMesh(
      this.rootGeometry,
      this.rootMaterial,
      withRoots.length,
    );
    const matrix = new THREE.Matrix4();
    withRoots.forEach((item, index) => {
      const radius = item.object.root_radius_max_m ?? 0;
      const terrainY = this.terrainElevationAt(
        item.object.local_x,
        -item.object.local_y,
      );
      matrix.compose(
        tempVector.set(
          item.object.local_x,
          terrainY + 0.025,
          -item.object.local_y,
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

  private rebuildOutlines(instances = this.currentInstances) {
    Object.values(this.outlineRoots).forEach(disposeInstancedWrappers);
    const groups: Record<keyof typeof this.outlineRoots, PlantInstance[]> = {
      selected: [],
      error: [],
      warning: [],
      hover: [],
    };
    for (const instance of instances) {
      const id = instance.object.object_id;
      if (this.selectedIds.has(id)) groups.selected.push(instance);
      else if (this.hoveredId === id) {
        const severity = this.issueSeverity.get(id);
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

  private objectAt(event: { clientX: number; clientY: number }) {
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

  private sourceCoordinateAt(event: {
    clientX: number;
    clientY: number;
  }): [number, number] | undefined {
    const snapshot = this.snapshot;
    const rect = this.canvas.getBoundingClientRect();
    if (!snapshot || !rect.width || !rect.height) return undefined;
    this.groundPointer.set(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.groundRaycaster.setFromCamera(this.groundPointer, this.camera);
    const terrain = this.contextRoot.getObjectByName(
      'confirmed-terrain:copernicus-glo90',
    );
    const terrainHit = terrain
      ? this.groundRaycaster.intersectObject(terrain, false)[0]
      : undefined;
    const point =
      terrainHit?.point ??
      this.groundRaycaster.ray.intersectPlane(
        new THREE.Plane(THREE.Object3D.DEFAULT_UP, 0),
        new THREE.Vector3(),
      );
    return point
      ? worldCenterToSource(point.x, point.z, snapshot.coordinate_origin)
      : undefined;
  }

  private handleClick = (event: MouseEvent) => {
    if (this.relocateSelection) {
      const coordinate = this.sourceCoordinateAt(event);
      this.setRelocateSelection(false);
      if (coordinate) this.onMoveTarget?.(coordinate);
      return;
    }
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
    this.pendingPointer = { clientX: event.clientX, clientY: event.clientY };
    if (this.pointerFrame) return;
    this.pointerFrame = requestAnimationFrame(() => {
      this.pointerFrame = 0;
      const pointer = this.pendingPointer;
      if (!pointer || this.interacting) return;
      if (this.hoverTimer) clearTimeout(this.hoverTimer);
      if (this.hoveredId) {
        this.hoveredId = undefined;
        this.onHover?.({ clientX: pointer.clientX, clientY: pointer.clientY });
        this.rebuildOutlines();
        this.requestRender(90);
      }
      const anchor = { ...pointer };
      this.hoverTimer = setTimeout(() => {
        this.hoverTimer = undefined;
        if (this.interacting) return;
        const objectId = this.objectAt(anchor);
        if (!objectId) return;
        this.hoveredId = objectId;
        this.onHover?.({
          objectId,
          clientX: anchor.clientX,
          clientY: anchor.clientY,
        });
        this.rebuildOutlines();
        this.requestRender(90);
      }, 160);
    });
  };

  private handlePointerLeave = (event: PointerEvent) => {
    if (this.hoverTimer) clearTimeout(this.hoverTimer);
    this.hoverTimer = undefined;
    this.onHover?.({ clientX: event.clientX, clientY: event.clientY });
    if (!this.hoveredId) return;
    this.hoveredId = undefined;
    this.rebuildOutlines();
    this.requestRender(90);
  };

  private handleControlStart = () => {
    if (this.hoverTimer) clearTimeout(this.hoverTimer);
    this.hoverTimer = undefined;
    this.interacting = true;
    this.cameraState = 'custom';
    this.hoveredId = undefined;
    this.onHover?.({ clientX: 0, clientY: 0 });
    this.rebuildOutlines();
    this.requestRender(160);
  };
  private handleControlChange = () => {
    this.constrainNavigationToSite();
    // Damping keeps moving the camera after pointer-up. Publish every actual
    // controls change so diagnostics and a mode switch never observe the
    // earlier mouse-up position.
    this.publishPlanViewState();
    this.requestRender(120);
  };
  private handleControlEnd = () => {
    this.interacting = false;
    this.constrainNavigationToSite();
    this.rebuildPlants();
    this.publishPlanViewState();
    this.requestRender(420);
  };

  private constrainNavigationToSite() {
    if (this.navigationBounds.isEmpty()) return;
    const margin = Math.max(
      8,
      Math.max(
        this.navigationBounds.max.x - this.navigationBounds.min.x,
        this.navigationBounds.max.z - this.navigationBounds.min.z,
      ) * 0.08,
    );
    const nextX = THREE.MathUtils.clamp(
      this.controls.target.x,
      this.navigationBounds.min.x - margin,
      this.navigationBounds.max.x + margin,
    );
    const nextZ = THREE.MathUtils.clamp(
      this.controls.target.z,
      this.navigationBounds.min.z - margin,
      this.navigationBounds.max.z + margin,
    );
    if (nextX === this.controls.target.x && nextZ === this.controls.target.z)
      return;
    const deltaX = nextX - this.controls.target.x;
    const deltaZ = nextZ - this.controls.target.z;
    this.controls.target.x = nextX;
    this.controls.target.z = nextZ;
    this.camera.position.x += deltaX;
    this.camera.position.z += deltaZ;
  }

  private resize = () => {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const drawingBuffer = sceneDrawingBufferSize(
      rect.width,
      rect.height,
      window.devicePixelRatio,
      this.qualityState.renderScale,
    );
    this.effectivePixelRatio = drawingBuffer.effectivePixelRatio;
    this.renderer.setPixelRatio(this.effectivePixelRatio);
    this.renderer.setSize(rect.width, rect.height, false);
    this.applyPendingPlanViewState();
    this.composer.setPixelRatio(this.effectivePixelRatio);
    this.composer.setSize(rect.width, rect.height);
    // OutlinePass operates on a half-resolution mask. Convert the design
    // token from CSS pixels to its physical mask radius so it remains
    // visually stable across DPR and adaptive quality changes.
    this.outlinePass.edgeThickness = THREE.MathUtils.clamp(
      sceneOutlineBufferWidth(this.effectivePixelRatio) / 2,
      0.75,
      8,
    );
    this.camera.aspect = rect.width / rect.height;
    this.camera.updateProjectionMatrix();
    this.csm?.updateFrustums();
    this.rebuildPlants();
    this.requestRender(140);
  };

  private requestRender(duration = 120) {
    if (this.disposed) return;
    this.animationStartedAt = Math.max(
      this.animationStartedAt,
      performance.now() + duration,
    );
    if (!this.frame && !this.rendering) {
      // A demand-render sequence starts with a baseline frame. The elapsed
      // idle time since the previous user action is never an FPS sample.
      this.sampleNextFrame = false;
      this.lastFrameAt = 0;
      this.frame = requestAnimationFrame(this.render);
    }
  }

  private render = (now: number) => {
    const sampleThisFrame = this.sampleNextFrame;
    this.frame = 0;
    if (this.disposed) return;
    this.rendering = true;
    let rebuildAfterCameraUpdate = false;
    if (this.cameraAnimation) {
      const elapsed = now - this.cameraAnimation.startedAt;
      const progress = Math.min(1, elapsed / this.cameraAnimation.duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      this.camera.position.lerpVectors(
        this.cameraAnimation.fromPosition,
        this.cameraAnimation.toPosition,
        eased,
      );
      this.controls.target.lerpVectors(
        this.cameraAnimation.fromTarget,
        this.cameraAnimation.toTarget,
        eased,
      );
      if (progress >= 1) {
        this.cameraAnimation = undefined;
        rebuildAfterCameraUpdate = true;
      }
    }
    this.controls.update();
    if (rebuildAfterCameraUpdate) this.rebuildPlants();
    this.refreshBillboards(this.plantRoot);
    this.refreshBillboards(this.pickRoot);
    Object.values(this.outlineRoots).forEach((root) =>
      this.refreshBillboards(root),
    );
    this.csm?.update();
    this.renderer.shadowMap.needsUpdate = true;
    this.renderer.info.reset();
    this.composer.render();
    this.recordFrame(now, sampleThisFrame);
    const keepRendering = Boolean(
      this.interacting || this.cameraAnimation || now < this.animationStartedAt,
    );
    this.rendering = false;
    if (keepRendering && !this.frame) {
      this.sampleNextFrame = true;
      this.frame = requestAnimationFrame(this.render);
    } else if (!this.frame) {
      this.sampleNextFrame = false;
      this.lastFrameAt = 0;
      // MapControls continues applying damping after its `end` event. Publish
      // once the demand-render burst is actually idle so 2D receives the
      // settled camera target rather than the mouse-up intermediate value.
      this.publishPlanViewState();
    }
  };

  private recordFrame(now: number, contiguous: boolean) {
    const duration =
      contiguous && this.lastFrameAt > 0 ? now - this.lastFrameAt : 0;
    this.lastFrameAt = now;
    if (duration > 0 && duration < 250) {
      this.frameSamples.push(duration);
      if (this.frameSamples.length > 240) this.frameSamples.shift();
    }
    if (now - this.telemetryFrame > 500) {
      this.telemetryFrame = now;
      this.adjustQuality(now);
      this.emitTelemetry();
    }
  }

  private adjustQuality(now: number) {
    if (this.frameSamples.length < 30) return;
    const durationMs = this.frameSamples.reduce(
      (sum, duration) => sum + duration,
      0,
    );
    const next = nextSceneQualityState(
      this.qualityState,
      sceneFrameWindow(this.frameSamples, durationMs, now),
    );
    if (next.renderScale === this.qualityState.renderScale) return;
    this.qualityState = next;
    // Measurements from the old drawing-buffer resolution cannot influence
    // the next quality decision.
    this.frameSamples.length = 0;
    this.lastFrameAt = now;
    this.resize();
  }

  private emitTelemetry() {
    if (!this.onTelemetry) return;
    const averageDuration = this.frameSamples.length
      ? this.frameSamples.reduce((sum, duration) => sum + duration, 0) /
        this.frameSamples.length
      : 0;
    this.onTelemetry({
      rendererGeneration: this.rendererGeneration,
      // Zero means that the sampler is warming up. Never report the target as
      // though it had been measured before a real frame interval exists.
      fps:
        averageDuration > 0
          ? Math.min(
              SCENE_RENDER_TARGETS.targetFps,
              Math.round(1000 / averageDuration),
            )
          : 0,
      drawCalls: this.renderer.info.render.calls,
      triangles: this.renderer.info.render.triangles,
      renderScale: this.qualityState.renderScale,
      effectivePixelRatio: Number(this.effectivePixelRatio.toFixed(3)),
      outlineWidthBufferPx: Number(
        sceneOutlineBufferWidth(this.effectivePixelRatio).toFixed(3),
      ),
      geometries: this.renderer.info.memory.geometries,
      textures: this.renderer.info.memory.textures,
      programs: this.renderer.info.programs?.length ?? 0,
      loadedPlantModels: this.assetLibrary.loadedModelCount(),
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
    if (this.pointerFrame) cancelAnimationFrame(this.pointerFrame);
    if (this.hoverTimer) clearTimeout(this.hoverTimer);
    this.resizeObserver.disconnect();
    this.canvas.removeEventListener('click', this.handleClick);
    this.canvas.removeEventListener('dblclick', this.handleDoubleClick);
    this.canvas.removeEventListener('pointermove', this.handlePointerMove);
    this.canvas.removeEventListener('pointerleave', this.handlePointerLeave);
    this.controls.removeEventListener('start', this.handleControlStart);
    this.controls.removeEventListener('change', this.handleControlChange);
    this.controls.removeEventListener('end', this.handleControlEnd);
    this.controls.dispose();

    // Remove CSM hooks while every configured material is still alive. The
    // light shadow targets are not owned by CSM.dispose(), so release those
    // explicitly before dropping the WebGL context.
    const csm = this.csm;
    if (csm) {
      csm.remove();
      csm.dispose();
      csm.lights.forEach((light) => light.shadow.dispose());
      this.csm = undefined;
    }

    // Instanced meshes own instance buffers only; their geometry/materials
    // are shared with PlantAssetLibrary and must be disposed exactly once by
    // that library. Root and outline resources are controller-owned instead.
    disposeInstancedWrappers(this.plantRoot);
    disposeInstancedWrappers(this.pickRoot);
    disposeInstancedWrappers(this.rootsRoot);
    Object.values(this.outlineRoots).forEach(disposeInstancedWrappers);
    this.disposeContext();
    this.outlinePass.dispose();
    this.outputPass.dispose();
    this.outlineProxyMaterial.dispose();
    this.rootGeometry.dispose();
    this.rootMaterial.dispose();
    this.pickTarget.dispose();
    this.whiteAlphaTexture.dispose();
    this.composer.dispose();
    this.scene.environment = null;
    this.environmentTexture?.dispose();
    this.environmentTexture = undefined;
    this.assetUnsubscribe?.();
    this.assetUnsubscribe = undefined;
    disposeObjectTree(this.workZonesRoot);
    this.scene.clear();
    this.renderer.setRenderTarget(null);
    this.renderer.renderLists.dispose();
    this.renderer.info.reset();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.canvas.width = 1;
    this.canvas.height = 1;
  }
}

export type { SceneCameraMode, SceneHover };
