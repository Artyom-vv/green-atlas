import { SceneCamera, type SceneCameraMode } from './SceneCamera';
import { StreetCameraGuide } from './StreetCameraGuide';
import {
  streetDirectionTarget,
  type StreetCameraPlacementState,
  type StreetCameraPoint,
} from '../../model/streetCameraPlacement';
import {
  SceneEnvironmentLayers,
  preparationMatchesSnapshot,
  canRetainDisplayGeometry,
  type ScenePreparedPreview,
} from './SceneEnvironmentLayers';
import {
  ScenePlantLayers,
  plantMatrix,
  type PlantInstance,
} from './ScenePlantLayers';
import type {
  PlantingZoneAssignment,
  ScenePlantObject,
  SceneSnapshot,
  ValidationIssue,
} from '@green/api-client';
import * as THREE from 'three';
import { CSM } from 'three/examples/jsm/csm/CSM.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { PlantAssetLibrary } from '@/widgets/scene/adapters/three/plantAssets';
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
  worldCenterToSource,
  type PlanViewState,
} from '@/entities/editor/model/planViewState';

type SceneSnapshotWithContext = SceneSnapshot & {
  context_features?: SceneContextLike[];
  display_geometry_sha256?: string;
};

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

const minimumPlantDiameterPx = 1.25;
let rendererGenerationSequence = 0;

export function createSceneRenderer(canvas: HTMLCanvasElement) {
  try {
    return new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: false,
      powerPreference: 'high-performance',
      preserveDrawingBuffer: false,
      // CAD scenes span kilometres while lawn and the model datum are only
      // millimetres apart. Linear perspective depth loses that separation
      // at overview distances and makes the datum flicker through surfaces.
      logarithmicDepthBuffer: true,
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
        logarithmicDepthBuffer: true,
      });
    } catch (fallbackError) {
      throw new AggregateError(
        [primaryError, fallbackError],
        'Unable to create a WebGL renderer',
      );
    }
  }
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

export class SceneController {
  private readonly canvas: HTMLCanvasElement;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly composer: EffectComposer;
  private readonly plants: ScenePlantLayers;
  private readonly navigation: SceneCamera;
  private readonly csmMaterials = new WeakSet<THREE.Material>();
  private readonly scene = new THREE.Scene();
  private readonly contextRoot = new THREE.Group();
  private readonly workZonesRoot = new THREE.Group();
  private readonly streetGuide = new StreetCameraGuide();
  private environmentLayers = new SceneEnvironmentLayers();
  private pendingEnvironmentLayers?: SceneEnvironmentLayers;
  private environmentPreparation?: ScenePreparedPreview;
  private environmentGeneration = 0;
  private readonly outputPass: OutputPass;
  private readonly groundRaycaster = new THREE.Raycaster();
  private readonly groundPointer = new THREE.Vector2();
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
  private assetLibrary: PlantAssetLibrary;
  private assetUnsubscribe?: () => void;
  private snapshot?: SceneSnapshotWithContext;
  private rootsVisible = false;
  private hoveredId?: string;
  private frame = 0;
  private rendering = false;
  private frameWaiters = new Set<(rendered: boolean) => void>();
  private sampleNextFrame = false;
  private pointerFrame = 0;
  private hoverTimer?: ReturnType<typeof setTimeout>;
  private pendingPointer?: { clientX: number; clientY: number };
  private pointerDown?: { clientX: number; clientY: number };
  private interacting = false;
  private telemetryFrame = 0;
  private animationStartedAt = 0;
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
  private terrainElevationAt: (x: number, z: number) => number = () => 0;
  private relocateSelection = false;
  constructor(options: SceneControllerOptions) {
    this.canvas = options.canvas;
    this.assetLibrary = options.assetLibrary;
    this.onSelect = options.onSelect;
    this.onMoveTarget = options.onMoveTarget;
    this.onHover = options.onHover;
    this.onTelemetry = options.onTelemetry;
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

    this.navigation = new SceneCamera(this.canvas, {
      initialViewState: options.initialViewState,
      onViewStateChange: options.onViewStateChange,
      onMove: (duration) => {
        if (!duration) this.rebuildPlants();
        this.requestRender(duration ? duration + 120 : 120);
      },
      onClipChange: (maxFar) => {
        if (!this.csm) return;
        this.csm.maxFar = maxFar;
        this.csm.updateFrustums();
      },
    });
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.navigation.camera));
    // OutlinePass owns several full/half/quarter resolution targets and
    // performs another depth render. One shared pass keeps the scene below
    // the GPU-memory budget; the active semantic class is selected below.
    this.plants = new ScenePlantLayers(
      this.canvas,
      this.navigation.camera,
      this.renderer,
      this.scene,
    );
    this.composer.addPass(this.plants.outlinePass);
    this.outputPass = new OutputPass();
    this.composer.addPass(this.outputPass);

    this.scene.background = new THREE.Color(0xf2f5f7);
    // Atmospheric depth should separate planes, not erase a city-scale plan.
    // A denser value made the truthful DXF context and young plants disappear
    // in the default overview on 700–1000 m scenes.
    this.scene.fog = new THREE.FogExp2(0xf2f5f7, 0.000045);
    this.plants.attach();
    this.contextRoot.name = 'context-root';
    this.scene.add(
      this.contextRoot,
      this.workZonesRoot,
      this.streetGuide.root,
      this.environmentLayers.root,
      this.plants.plantRoot,
      this.plants.rootsRoot,
    );
    this.addEnvironment();

    this.canvas.addEventListener('click', this.handleClick);
    this.canvas.addEventListener('pointerdown', this.handlePointerDown);
    this.canvas.addEventListener('dblclick', this.handleDoubleClick);
    this.canvas.addEventListener('pointermove', this.handlePointerMove, {
      passive: true,
    });
    this.canvas.addEventListener('pointerleave', this.handlePointerLeave, {
      passive: true,
    });
    this.navigation.controls.addEventListener('start', this.handleControlStart);
    this.navigation.controls.addEventListener(
      'change',
      this.handleControlChange,
    );
    this.navigation.controls.addEventListener('end', this.handleControlEnd);
    this.resizeObserver = new ResizeObserver(this.resize);
    this.resizeObserver.observe(this.canvas);
    this.resize();
    this.requestRender(280);
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
      camera: this.navigation.camera,
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
  private detachCsmMaterials(
    root: THREE.Object3D,
    owned: Iterable<THREE.Material> = [],
  ) {
    const csm = this.csm;
    if (!csm) return;
    const detach = (material: THREE.Material) => {
      if (!this.csmMaterials.delete(material)) return;
      delete (material as unknown as { onBeforeCompile?: unknown })
        .onBeforeCompile;
      if (material.defines) {
        delete material.defines.USE_CSM;
        delete material.defines.CSM_CASCADES;
        delete material.defines.CSM_FADE;
      }
      csm.shaders.delete(material);
      material.needsUpdate = true;
    };
    root.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return;
      const materials = Array.isArray(object.material)
        ? object.material
        : [object.material];
      materials.forEach(detach);
    });
    for (const material of owned) detach(material);
  }

  private disposeContext() {
    for (const child of [...this.contextRoot.children]) {
      this.contextRoot.remove(child);
      this.detachCsmMaterials(child);
      disposeObjectTree(child);
    }
  }

  private clearPreparedEnvironment() {
    this.environmentGeneration++;
    this.pendingEnvironmentLayers?.dispose();
    this.pendingEnvironmentLayers = undefined;
    this.detachCsmMaterials(this.plants.plantRoot);
    this.plants.rebuild([]);
    this.currentInstances = [];
    this.rebuildOutlines();
    this.detachCsmMaterials(
      this.environmentLayers.root,
      this.environmentLayers.ownedMaterials(),
    );
    this.environmentLayers.clear();
    this.environmentPreparation = undefined;
  }

  private rebuildContext() {
    const snapshot = this.snapshot;
    if (!snapshot) return;
    this.disposeContext();
    const suppressed = new Set(
      this.environmentPreparation?.preview.suppressed_source_ids ?? [],
    );
    const context = buildContextScene(
      (snapshot.context_features ?? []).filter(
        (feature) => !suppressed.has(feature.feature_id),
      ),
      snapshot.vertical_primitives ?? [],
    );
    this.terrainElevationAt = context.terrainElevationAt;
    this.navigation.setBasis(
      snapshot.coordinate_origin ?? [0, 0],
      this.terrainElevationAt,
    );
    this.contextRoot.add(context.group);
    this.contextRoot.updateWorldMatrix(true, true);
    this.navigation.bounds
      .setFromObject(this.contextRoot)
      .union(this.environmentLayers.bounds)
      .union(this.navigation.boundsForObjects(snapshot.objects));
    this.configureCsmMaterials(this.contextRoot);
    this.configureCsmMaterials(this.environmentLayers.root);
  }

  async updateEnvironment(preparation?: ScenePreparedPreview) {
    if (this.disposed) return false;
    if (
      this.environmentPreparation &&
      this.snapshot &&
      !preparationMatchesSnapshot(this.environmentPreparation, this.snapshot) &&
      !canRetainDisplayGeometry(this.environmentPreparation, this.snapshot)
    ) {
      this.clearPreparedEnvironment();
      this.rebuildContext();
      this.rebuildPlants();
      this.requestRender(120);
    }
    if (
      preparation &&
      this.environmentPreparation?.id === preparation.id &&
      this.environmentPreparation.sha256 === preparation.sha256 &&
      this.snapshot &&
      preparationMatchesSnapshot(preparation, this.snapshot) &&
      this.environmentLayers.root.children.length
    ) {
      await this.environmentLayers.whenReady();
      return (
        !this.disposed && this.environmentPreparation?.id === preparation.id
      );
    }
    if (!preparation) {
      this.clearPreparedEnvironment();
      this.rebuildContext();
      this.rebuildPlants();
      this.requestRender(120);
      return false;
    }
    if (
      this.snapshot &&
      !preparationMatchesSnapshot(preparation, this.snapshot)
    )
      return false;

    const generation = ++this.environmentGeneration;
    this.pendingEnvironmentLayers?.dispose();
    const staged = this.environmentLayers.createStaging();
    this.pendingEnvironmentLayers = staged;
    try {
      // Load every required resource off-screen. Keep the displayed geometry,
      // materials and editable plant wrappers until the replacement is ready.
      staged.update(preparation);
      await staged.whenReady();
      if (
        this.disposed ||
        generation !== this.environmentGeneration ||
        !this.snapshot ||
        !preparationMatchesSnapshot(preparation, this.snapshot)
      ) {
        staged.dispose();
        return false;
      }
      this.pendingEnvironmentLayers = undefined;
      const previous = this.environmentLayers;
      this.clearPreparedEnvironment();
      this.scene.remove(previous.root);
      previous.dispose();
      this.environmentLayers = staged;
      this.environmentPreparation = preparation;
      this.scene.add(staged.root);
      this.rebuildContext();
      this.rebuildPlants();
      this.requestRender(260);
      return true;
    } catch (error) {
      staged.dispose();
      if (generation !== this.environmentGeneration || this.disposed)
        return false;
      this.pendingEnvironmentLayers = undefined;
      // A failed replacement must not downgrade an already valid scene.
      throw error;
    }
  }

  updateSnapshot(
    snapshot: SceneSnapshotWithContext,
    issues: ValidationIssue[] = [],
  ) {
    const firstSnapshot = !this.snapshot;
    const firstPopulatedSnapshot =
      !this.snapshot?.objects.length && snapshot.objects.length > 0;
    this.snapshot = snapshot;
    this.objectById.clear();
    snapshot.objects.forEach((object) =>
      this.objectById.set(object.object_id, object),
    );
    this.issueSeverity.clear();
    severityByObject(issues).forEach((severity, id) =>
      this.issueSeverity.set(id, severity),
    );

    if (this.environmentPreparation) {
      if (
        !preparationMatchesSnapshot(this.environmentPreparation, snapshot) &&
        !canRetainDisplayGeometry(this.environmentPreparation, snapshot)
      )
        this.clearPreparedEnvironment();
      else if (!this.environmentLayers.root.children.length)
        this.environmentLayers.update(this.environmentPreparation);
    }
    this.rebuildContext();
    this.rebuildPlants();
    if (
      this.navigation.hasPendingView &&
      this.navigation.applyPendingPlanViewState()
    ) {
      // The incoming 2D viewport is authoritative on first render.
    } else if (firstSnapshot || firstPopulatedSnapshot)
      this.fitPrimaryPlanting(false);
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
    this.plants.rebuildRoots(
      this.currentInstances,
      this.rootsVisible,
      this.terrainElevationAt,
    );
    this.requestRender(180);
  }

  setCameraMode(mode: SceneCameraMode) {
    this.navigation.mode = mode;
    if (mode === 'overview') this.fitAll(true);
    else this.focusSelection(true);
  }

  fitPlantings() {
    this.navigation.mode = 'overview';
    if (this.snapshot)
      this.navigation.fitBounds(
        this.navigation.boundsForObjects(this.snapshot.objects),
        true,
      );
  }

  zoom(factor: number) {
    this.navigation.zoom(factor);
  }
  fitExtent(extent: readonly number[]) {
    this.navigation.fitExtent(extent);
  }
  importPlanViewState(state: PlanViewState) {
    this.navigation.importPlanViewState(state);
  }
  exportPlanViewState() {
    return this.navigation.exportPlanViewState();
  }
  exportRenderCamera() {
    return this.navigation.exportRenderCamera();
  }

  private streetPlacement?: {
    onPlaced: () => void;
    onChange?: (state: StreetCameraPlacementState) => void;
    state: StreetCameraPlacementState;
  };
  beginStreetPlacement(
    onPlaced: () => void,
    onChange?: (state: StreetCameraPlacementState) => void,
  ) {
    this.cancelStreetPlacement();
    this.streetPlacement = {
      onPlaced,
      onChange,
      state: { step: 'position', snapped: false },
    };
    this.canvas.style.cursor = 'crosshair';
    if (this.hoverTimer) clearTimeout(this.hoverTimer);
    this.hoveredId = undefined;
    this.onHover?.({ clientX: 0, clientY: 0 });
    this.rebuildOutlines();
    onChange?.(this.streetPlacement.state);
    this.canvas.focus();
  }
  resetStreetPlacement() {
    if (!this.streetPlacement) return;
    this.streetPlacement.state = { step: 'position', snapped: false };
    this.streetPlacement.onChange?.(this.streetPlacement.state);
    this.streetGuide.clear();
    this.requestRender(90);
  }
  confirmStreetPlacement() {
    const placement = this.streetPlacement;
    if (
      !placement ||
      placement.state.step !== 'ready' ||
      !placement.state.position ||
      !placement.state.target
    )
      return false;
    if (
      !this.navigation.placeStreetCamera(
        placement.state.position,
        placement.state.target,
      )
    )
      return false;
    this.cancelStreetPlacement();
    placement.onPlaced();
    return true;
  }
  levelStreetHorizon() {
    return this.navigation.levelStreetHorizon();
  }
  cancelStreetPlacement() {
    this.streetPlacement = undefined;
    this.streetGuide.clear();
    this.canvas.style.removeProperty('cursor');
    this.requestRender(90);
  }

  private showStreetGuide(
    position?: StreetCameraPoint,
    target?: StreetCameraPoint,
  ) {
    const world = (point?: StreetCameraPoint) => {
      if (!point || !this.snapshot) return undefined;
      const [x, z] = sourceCenterToWorld(
        point,
        this.snapshot.coordinate_origin,
      );
      return new THREE.Vector3(x, this.terrainElevationAt(x, z) + 0.2, z);
    };
    this.streetGuide.update(
      world(position),
      world(target),
      this.navigation.camera.fov,
      this.navigation.camera.aspect,
    );
    this.requestRender(90);
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
    this.navigation.mode = 'overview';
    this.navigation.fitBounds(this.navigation.boundsForObjects(selected), true);
  }

  private fitPrimaryPlanting(animate = true) {
    if (!this.snapshot?.objects.length) {
      this.fitAll(animate);
      return;
    }
    this.navigation.fitBounds(
      this.navigation.boundsForObjects(this.snapshot.objects),
      animate,
    );
  }

  fitAll(animate = true) {
    if (!this.snapshot) return;
    const bounds = this.navigation.boundsForObjects(this.snapshot.objects);
    // When plants exist, the useful overview is the planting operation with
    // surrounding DXF context—not the entire imported sheet. The latter can
    // be substantially larger and used to reduce every young tree to a pixel.
    this.contextRoot.updateWorldMatrix(true, true);
    bounds.union(new THREE.Box3().setFromObject(this.contextRoot));
    this.navigation.fitBounds(bounds, animate);
  }

  focusSelection(groundLevel = false) {
    const selected = [...this.selectedIds]
      .map((id) => this.objectById.get(id))
      .find(Boolean);
    if (!selected) {
      this.fitAll(true);
      return;
    }
    this.navigation.focusObject(selected, groundLevel);
  }

  setRelocateSelection(enabled: boolean) {
    this.relocateSelection =
      enabled && this.selectedIds.size > 0 && Boolean(this.onMoveTarget);
    this.canvas.style.cursor = this.relocateSelection ? 'crosshair' : '';
  }

  private visibleObjects() {
    if (!this.snapshot) return [];
    this.navigation.camera.updateMatrixWorld();
    const projection = new THREE.Matrix4().multiplyMatrices(
      this.navigation.camera.projectionMatrix,
      this.navigation.camera.matrixWorldInverse,
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
      const diameterPx = this.navigation.projectedDiameter(object);
      if (
        diameterPx < minimumPlantDiameterPx &&
        !this.selectedIds.has(object.object_id) &&
        this.hoveredId !== object.object_id
      )
        continue;
      const previous = this.previousLod.get(object.object_id);
      const lod = classifySceneLod(diameterPx, previous);
      this.previousLod.set(object.object_id, lod);
      const prototype =
        this.environmentLayers.getPlannedPrototype(object.object_id, lod) ??
        this.assetLibrary.get(object, lod);
      instances.push({
        object,
        prototype,
        lod,
        matrix: plantMatrix(
          object,
          prototype,
          this.environmentLayers.getPlannedElevation(object.object_id) ??
            this.terrainElevationAt(object.local_x, -object.local_y),
          this.environmentLayers.getPlannedRotation(object.object_id),
        ),
      });
    }
    return instances;
  }

  private rebuildPlants() {
    this.environmentLayers.setCamera(
      this.navigation.camera,
      this.canvas.clientHeight,
    );
    const instances = this.collectInstances();
    this.currentInstances = instances;
    this.visiblePlantInstances = instances.length;
    this.plants.rebuild(instances);
    this.plants.rebuildRoots(
      instances,
      this.rootsVisible,
      this.terrainElevationAt,
    );
    this.rebuildOutlines();
    this.configureCsmMaterials(this.plants.plantRoot);
    // Publish renderer counters only after the requested rendered frame.
    this.telemetryFrame = 0;
  }

  private rebuildOutlines() {
    this.plants.rebuildOutlines(
      this.currentInstances,
      this.selectedIds,
      this.hoveredId,
      this.issueSeverity,
    );
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
    this.groundRaycaster.setFromCamera(
      this.groundPointer,
      this.navigation.camera,
    );
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

  private handlePointerDown = (event: PointerEvent) => {
    this.pointerDown = { clientX: event.clientX, clientY: event.clientY };
  };

  private selectStreetPoint(event: MouseEvent) {
    if (this.streetPlacement) {
      if (
        this.pointerDown &&
        Math.hypot(
          event.clientX - this.pointerDown.clientX,
          event.clientY - this.pointerDown.clientY,
        ) > 5
      )
        return;
      const coordinate = this.sourceCoordinateAt(event);
      if (!coordinate) return;
      const placement = this.streetPlacement;
      if (!placement.state.position) {
        placement.state = {
          step: 'direction',
          position: coordinate,
          snapped: false,
        };
      } else {
        const target = streetDirectionTarget(
          placement.state.position,
          coordinate,
          event.shiftKey,
        );
        if (!target) return;
        placement.state = {
          ...placement.state,
          step: 'ready',
          target,
          snapped: event.shiftKey,
        };
      }
      this.showStreetGuide(placement.state.position, placement.state.target);
      placement.onChange?.(placement.state);
    }
  }

  private handleClick = (event: MouseEvent) => {
    if (this.streetPlacement) {
      this.selectStreetPoint(event);
      return;
    }
    if (this.relocateSelection) {
      const coordinate = this.sourceCoordinateAt(event);
      this.setRelocateSelection(false);
      if (coordinate) this.onMoveTarget?.(coordinate);
      return;
    }
    const objectId = this.plants.objectAt(event);
    if (objectId) this.onSelect(objectId);
  };

  private handleDoubleClick = (event: MouseEvent) => {
    if (this.streetPlacement) return;
    const objectId = this.plants.objectAt(event);
    if (!objectId) return;
    this.onSelect(objectId);
    this.selectedIds.clear();
    this.selectedIds.add(objectId);
    this.rebuildOutlines();
    this.focusSelection(true);
  };

  private handlePointerMove = (event: PointerEvent) => {
    if (this.streetPlacement && !this.interacting) {
      const coordinate = this.sourceCoordinateAt(event);
      const { position, step } = this.streetPlacement.state;
      if (step !== 'ready') {
        const target =
          position && coordinate
            ? streetDirectionTarget(position, coordinate, event.shiftKey)
            : undefined;
        this.showStreetGuide(position ?? coordinate, target);
        const snapped = Boolean(target && event.shiftKey);
        if (this.streetPlacement.state.snapped !== snapped) {
          this.streetPlacement.state = {
            ...this.streetPlacement.state,
            snapped,
          };
          this.streetPlacement.onChange?.(this.streetPlacement.state);
        }
      }
      return;
    }
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
        const objectId = this.plants.objectAt(anchor);
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
    if (this.navigation.mode !== 'ground') this.navigation.mode = 'custom';
    this.hoveredId = undefined;
    this.onHover?.({ clientX: 0, clientY: 0 });
    this.rebuildOutlines();
    this.requestRender(160);
  };
  private handleControlChange = () => {
    this.navigation.constrainNavigationToSite();
    this.navigation.constrainStreetEyeHeight();
    // Damping keeps moving the camera after pointer-up. Publish every actual
    // controls change so diagnostics and a mode switch never observe the
    // earlier mouse-up position.
    this.navigation.publishPlanViewState();
    this.requestRender(120);
  };
  private handleControlEnd = () => {
    this.interacting = false;
    this.navigation.constrainNavigationToSite();
    this.rebuildPlants();
    this.navigation.publishPlanViewState();
    this.requestRender(420);
  };

  refreshViewport() {
    this.resize();
    this.requestRender(280);
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
    this.navigation.applyPendingPlanViewState();
    this.composer.setPixelRatio(this.effectivePixelRatio);
    this.composer.setSize(rect.width, rect.height);
    // OutlinePass operates on a half-resolution mask. Convert the design
    // token from CSS pixels to its physical mask radius so it remains
    // visually stable across DPR and adaptive quality changes.
    this.plants.outlinePass.edgeThickness = THREE.MathUtils.clamp(
      sceneOutlineBufferWidth(this.effectivePixelRatio) / 2,
      0.75,
      8,
    );
    this.navigation.camera.aspect = rect.width / rect.height;
    this.navigation.camera.updateProjectionMatrix();
    this.csm?.updateFrustums();
    this.rebuildPlants();
    this.requestRender(140);
  };

  /** Resolves after a frame with the current scene has actually been drawn. */
  whenRendered(): Promise<boolean> {
    if (this.disposed) return Promise.resolve(false);
    return new Promise((resolve) => {
      this.frameWaiters.add(resolve);
      this.requestRender();
    });
  }

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
    rebuildAfterCameraUpdate = this.navigation.advance(now);
    this.navigation.controls.update();
    this.navigation.constrainStreetEyeHeight();
    if (rebuildAfterCameraUpdate) this.rebuildPlants();
    this.streetGuide.refresh(this.navigation.camera, this.canvas.clientHeight);
    if (
      this.environmentLayers.setCamera(
        this.navigation.camera,
        this.canvas.clientHeight,
      )
    ) {
      this.rebuildPlants();
      this.configureCsmMaterials(this.environmentLayers.root);
    }
    this.plants.refreshBillboards();
    this.csm?.update();
    this.renderer.shadowMap.needsUpdate = true;
    this.renderer.info.reset();
    this.composer.render();
    const waiters = [...this.frameWaiters];
    this.frameWaiters.clear();
    for (const resolve of waiters) resolve(true);
    this.recordFrame(now, sampleThisFrame);
    const keepRendering = Boolean(
      this.interacting ||
      this.navigation.animating ||
      now < this.animationStartedAt,
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
      this.navigation.publishPlanViewState();
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
    for (const resolve of this.frameWaiters) resolve(false);
    this.frameWaiters.clear();
    if (this.disposed) return;
    this.disposed = true;
    if (this.frame) cancelAnimationFrame(this.frame);
    if (this.pointerFrame) cancelAnimationFrame(this.pointerFrame);
    if (this.hoverTimer) clearTimeout(this.hoverTimer);
    this.resizeObserver.disconnect();
    this.canvas.removeEventListener('click', this.handleClick);
    this.canvas.removeEventListener('pointerdown', this.handlePointerDown);
    this.canvas.removeEventListener('dblclick', this.handleDoubleClick);
    this.canvas.removeEventListener('pointermove', this.handlePointerMove);
    this.canvas.removeEventListener('pointerleave', this.handlePointerLeave);
    this.navigation.controls.removeEventListener(
      'start',
      this.handleControlStart,
    );
    this.navigation.controls.removeEventListener(
      'change',
      this.handleControlChange,
    );
    this.navigation.controls.removeEventListener('end', this.handleControlEnd);
    this.navigation.dispose();
    this.streetGuide.dispose();
    this.clearPreparedEnvironment();

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
    this.plants.clearInstances();
    this.disposeContext();
    this.plants.dispose();
    this.outputPass.dispose();
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
