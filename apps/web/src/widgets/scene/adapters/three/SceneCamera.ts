import * as THREE from 'three';
import { MapControls } from 'three/examples/jsm/controls/MapControls.js';
import type { RenderCamera, ScenePlantObject } from '@green/api-client';
import {
  sourceCenterToWorld,
  worldCenterToSource,
  validPlanViewState,
  writePlanViewStateAttributes,
  type PlanViewState,
} from '@/entities/editor/model/planViewState';

export type SceneCameraMode = 'overview' | 'ground';
type SceneCameraState = SceneCameraMode | 'custom';
const groundEyeHeightM = 1.7;
const CAMERA_MOTION_DURATION_MS = { fit: 680, focus: 620 };
const SITE_NAVIGATION_MARGIN = { minimumM: 8, spanRatio: 0.08 };
const tempVector = new THREE.Vector3();
interface SceneCameraOptions {
  initialViewState?: PlanViewState;
  onViewStateChange?: (state: PlanViewState) => void;
  onMove: (duration: number) => void;
  onClipChange: (maxFar: number) => void;
}
interface CameraClipping {
  near: number;
  far: number;
  shadowMaxFar?: number;
}

/** Owns the camera pose, view transfer and navigation. Rendering owns its own
 * frame/resource lifetime and reacts through the two narrow callbacks. */
export class SceneCamera {
  readonly camera = new THREE.PerspectiveCamera(42, 1, 0.08, 20_000);
  readonly controls: MapControls;
  readonly bounds = new THREE.Box3();
  mode: SceneCameraState = 'overview';
  private coordinateOrigin?: readonly number[];
  private pendingPlanViewState?: PlanViewState;
  private terrainElevationAt: (x: number, z: number) => number = () => 0;
  private readonly onMove: SceneCameraOptions['onMove'];
  private readonly onClipChange: SceneCameraOptions['onClipChange'];
  private shadowMaxFar?: number;
  private readonly onViewStateChange?: SceneCameraOptions['onViewStateChange'];
  private cameraAnimation?: {
    startedAt: number;
    duration: number;
    fromPosition: THREE.Vector3;
    toPosition: THREE.Vector3;
    fromTarget: THREE.Vector3;
    toTarget: THREE.Vector3;
    fromClipping: CameraClipping;
    toClipping: CameraClipping;
  };
  constructor(
    private readonly canvas: HTMLCanvasElement,
    options: SceneCameraOptions,
  ) {
    this.pendingPlanViewState = options.initialViewState;
    this.onMove = options.onMove;
    this.onClipChange = options.onClipChange;
    this.onViewStateChange = options.onViewStateChange;
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
  }
  setBasis(
    origin: readonly number[],
    terrainElevationAt: (x: number, z: number) => number,
  ) {
    this.coordinateOrigin = origin;
    this.terrainElevationAt = terrainElevationAt;
  }
  get hasPendingView() {
    return Boolean(this.pendingPlanViewState);
  }
  get animating() {
    return Boolean(this.cameraAnimation);
  }
  advance(now: number) {
    let completed = false;
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
        const clipping = this.cameraAnimation.toClipping;
        this.cameraAnimation = undefined;
        this.applyClipping(clipping);
        completed = true;
      } else this.refreshAnimationClipping();
    }
    return completed;
  }
  dispose() {
    this.controls.dispose();
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

  importPlanViewState(state: PlanViewState) {
    if (!validPlanViewState(state)) return;
    this.pendingPlanViewState = state;
    this.applyPendingPlanViewState();
  }

  publishPlanViewState() {
    const renderCamera = this.exportRenderCamera();
    if (renderCamera)
      this.canvas.setAttribute(
        'data-scene-render-camera',
        JSON.stringify(renderCamera),
      );
    const state = this.exportPlanViewState();
    if (!state) return;
    writePlanViewStateAttributes(this.canvas, state);
    this.onViewStateChange?.(state);
  }

  exportRenderCamera(): RenderCamera | undefined {
    const rect = this.canvas.getBoundingClientRect();
    if (!this.coordinateOrigin || rect.width <= 0 || rect.height <= 0) return;
    this.constrainStreetEyeHeight();
    this.refreshAnimationClipping();
    this.camera.updateMatrixWorld();
    return {
      position: this.camera.position.toArray(),
      quaternion: this.camera.quaternion.clone().normalize().toArray(),
      fov_y_deg: this.camera.fov,
      aspect: rect.width / rect.height,
      near: this.camera.near,
      far: this.camera.far,
    };
  }

  placeStreetCamera(
    sourceCoordinate: readonly [number, number],
    sourceTarget?: readonly [number, number],
  ) {
    if (!this.coordinateOrigin || !sourceCoordinate.every(Number.isFinite))
      return false;
    const [x, z] = sourceCenterToWorld(
      [sourceCoordinate[0], sourceCoordinate[1]],
      this.coordinateOrigin,
    );
    const direction = this.controls.target.clone().sub(this.camera.position);
    if (sourceTarget) {
      if (!sourceTarget.every(Number.isFinite)) return false;
      const [targetX, targetZ] = sourceCenterToWorld(
        [sourceTarget[0], sourceTarget[1]],
        this.coordinateOrigin,
      );
      direction.set(targetX - x, 0, targetZ - z);
      if (direction.lengthSq() < 0.25) return false;
    }
    direction.y = 0;
    if (direction.lengthSq() < 1e-8) direction.set(0, 0, -1);
    direction.normalize();
    const position = new THREE.Vector3(
      x,
      this.terrainElevationAt(x, z) + groundEyeHeightM,
      z,
    );
    const target = position.clone().addScaledVector(direction, 18);
    this.mode = 'ground';
    this.controls.minPolarAngle = 0.05;
    this.controls.maxPolarAngle = Math.PI - 0.05;
    this.controls.minDistance = 2;
    this.controls.maxDistance = Math.max(60, this.controls.maxDistance);
    this.camera.near = 0.04;
    this.camera.far = Math.max(2000, this.camera.far);
    this.camera.updateProjectionMatrix();
    this.onClipChange(Math.min(720, this.camera.far));
    this.moveCamera(position, target, 0);
    this.publishPlanViewState();
    return true;
  }

  levelStreetHorizon() {
    if (this.mode !== 'ground') return false;
    const direction = this.controls.target.clone().sub(this.camera.position);
    const distance = Math.max(2, direction.length());
    direction.y = 0;
    if (direction.lengthSq() < 1e-8) direction.set(0, 0, -1);
    direction.normalize();
    this.camera.up.set(0, 1, 0);
    this.constrainStreetEyeHeight();
    this.moveCamera(
      this.camera.position.clone(),
      this.camera.position.clone().addScaledVector(direction, distance),
      0,
    );
    this.publishPlanViewState();
    return true;
  }

  /** Preserve the direction when orbit/pan moves a street camera vertically. */
  constrainStreetEyeHeight() {
    if (this.mode !== 'ground' || this.cameraAnimation) return;
    const elevation = this.terrainElevationAt(
      this.camera.position.x,
      this.camera.position.z,
    );
    const delta = elevation + groundEyeHeightM - this.camera.position.y;
    if (Math.abs(delta) < 1e-8) return;
    this.camera.position.y += delta;
    this.controls.target.y += delta;
    this.camera.updateMatrixWorld(true);
  }

  exportPlanViewState(): PlanViewState | undefined {
    if (!this.coordinateOrigin) return undefined;
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
      this.coordinateOrigin ?? [0, 0],
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

  applyPendingPlanViewState() {
    const state = this.pendingPlanViewState;
    const origin = this.coordinateOrigin;
    const rect = this.canvas.getBoundingClientRect();
    if (!state || !origin || rect.width <= 0 || rect.height <= 0) return false;
    this.restoreOverviewControls();
    const [worldX, worldZ] = sourceCenterToWorld(state.center, origin);
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
    this.mode = 'custom';
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

  boundsForObjects(objects: readonly ScenePlantObject[]) {
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

  fitBounds(bounds: THREE.Box3, animate: boolean) {
    this.restoreOverviewControls();
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
    const far = Math.max(120, this.controls.maxDistance + span * 1.5);
    this.moveCamera(
      position,
      target,
      animate ? CAMERA_MOTION_DURATION_MS.fit : 0,
      {
        near: THREE.MathUtils.clamp(span / 700, 0.06, 1.2),
        far,
        shadowMaxFar: Math.min(
          far,
          THREE.MathUtils.clamp(span * 2.2, 100, 720),
        ),
      },
    );
  }

  private restoreOverviewControls() {
    this.controls.minPolarAngle = 0;
    this.controls.maxPolarAngle = Math.PI / 2.1;
    this.controls.minDistance = 1.2;
  }

  private moveCamera(
    position: THREE.Vector3,
    target: THREE.Vector3,
    duration: number,
    clipping: CameraClipping = {
      near: this.camera.near,
      far: this.camera.far,
    },
  ) {
    if (!duration) {
      this.cameraAnimation = undefined;
      this.camera.position.copy(position);
      this.controls.target.copy(target);
      this.applyClipping(clipping);
      this.controls.update();
      this.onMove(0);
      return;
    }
    this.cameraAnimation = {
      startedAt: performance.now(),
      duration,
      fromPosition: this.camera.position.clone(),
      toPosition: position,
      fromTarget: this.controls.target.clone(),
      toTarget: target,
      fromClipping: { near: this.camera.near, far: this.camera.far },
      toClipping: clipping,
    };
    this.refreshAnimationClipping();
    this.onMove(duration);
  }

  private applyClipping(clipping: CameraClipping) {
    const changed =
      this.camera.near !== clipping.near || this.camera.far !== clipping.far;
    if (changed) {
      this.camera.near = clipping.near;
      this.camera.far = clipping.far;
      this.camera.updateProjectionMatrix();
    }
    if (
      clipping.shadowMaxFar !== undefined &&
      (changed || this.shadowMaxFar !== clipping.shadowMaxFar)
    ) {
      this.shadowMaxFar = clipping.shadowMaxFar;
      this.onClipChange(clipping.shadowMaxFar);
    }
  }

  private refreshAnimationClipping() {
    const animation = this.cameraAnimation;
    if (!animation) return;
    // The camera may still be kilometres from the destination before the
    // next animation frame. Keep its actual pose renderable, including when
    // that pose is exported to Blender before the transition completes.
    this.applyClipping({
      near: Math.min(animation.fromClipping.near, animation.toClipping.near),
      far: Math.max(
        animation.fromClipping.far,
        animation.toClipping.far +
          this.camera.position.distanceTo(animation.toPosition),
      ),
      shadowMaxFar: animation.toClipping.shadowMaxFar,
    });
  }

  projectedDiameter(object: ScenePlantObject) {
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

  constrainNavigationToSite() {
    if (this.bounds.isEmpty()) return;
    const margin = Math.max(
      SITE_NAVIGATION_MARGIN.minimumM,
      Math.max(
        this.bounds.max.x - this.bounds.min.x,
        this.bounds.max.z - this.bounds.min.z,
      ) * SITE_NAVIGATION_MARGIN.spanRatio,
    );
    const nextX = THREE.MathUtils.clamp(
      this.controls.target.x,
      this.bounds.min.x - margin,
      this.bounds.max.x + margin,
    );
    const nextZ = THREE.MathUtils.clamp(
      this.controls.target.z,
      this.bounds.min.z - margin,
      this.bounds.max.z + margin,
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

  fitExtent(extent: readonly number[]) {
    if (
      !this.coordinateOrigin ||
      extent.length !== 4 ||
      !extent.every(Number.isFinite)
    )
      return;
    const origin = this.coordinateOrigin ?? [0, 0];
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

  focusObject(selected: ScenePlantObject, groundLevel: boolean) {
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
    const far = Math.max(120, distance * 10);
    this.moveCamera(position, target, CAMERA_MOTION_DURATION_MS.focus, {
      near: groundLevel ? 0.04 : 0.06,
      far,
      shadowMaxFar: Math.min(far, THREE.MathUtils.clamp(distance * 5, 60, 280)),
    });
  }
}
