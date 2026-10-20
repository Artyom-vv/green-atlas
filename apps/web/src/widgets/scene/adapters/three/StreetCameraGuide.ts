import * as THREE from 'three';

/** Selection-only guide, excluded from source geometry and render exports. */
export class StreetCameraGuide {
  readonly root = new THREE.Group();
  private readonly position = this.marker(0x225cff);
  private readonly target = this.marker(0xf59e0b);
  private readonly line = new THREE.LineSegments(
    new THREE.BufferGeometry().setAttribute(
      'position',
      new THREE.BufferAttribute(new Float32Array(30), 3),
    ),
    new THREE.LineBasicMaterial({
      color: 0x225cff,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    }),
  );

  constructor() {
    this.root.name = 'street-camera-placement-guide';
    this.line.renderOrder = 1000;
    this.line.frustumCulled = false;
    this.root.add(this.position, this.target, this.line);
    this.clear();
  }

  private marker(color: number) {
    const group = new THREE.Group();
    const background = new THREE.Mesh(
      new THREE.CircleGeometry(1.15, 32),
      new THREE.MeshBasicMaterial({
        color: 0xffffff,
        depthTest: false,
        depthWrite: false,
        toneMapped: false,
        side: THREE.DoubleSide,
      }),
    );
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.48, 1, 32),
      new THREE.MeshBasicMaterial({
        color,
        depthTest: false,
        depthWrite: false,
        toneMapped: false,
        side: THREE.DoubleSide,
      }),
    );
    background.renderOrder = 1001;
    ring.renderOrder = 1002;
    group.add(background, ring);
    return group;
  }

  update(
    position?: THREE.Vector3,
    target?: THREE.Vector3,
    fov = 42,
    aspect = 1,
  ) {
    this.root.visible = Boolean(position);
    this.position.visible = Boolean(position);
    this.target.visible = Boolean(target);
    this.line.visible = Boolean(position && target);
    if (!position) return;
    this.position.position.copy(position);
    if (!target) return;
    this.target.position.copy(target);
    const direction = target.clone().sub(position);
    direction.y = 0;
    const distance = direction.length();
    direction.normalize();
    const right = new THREE.Vector3(-direction.z, 0, direction.x);
    const halfWidth =
      distance * Math.tan(THREE.MathUtils.degToRad(fov) / 2) * aspect;
    const centre = position.clone().addScaledVector(direction, distance);
    const left = centre.clone().addScaledVector(right, -halfWidth);
    const rightEdge = centre.clone().addScaledVector(right, halfWidth);
    const arrowLength = Math.min(distance * 0.18, 2.5);
    const arrowBase = target.clone().addScaledVector(direction, -arrowLength);
    const arrowLeft = arrowBase
      .clone()
      .addScaledVector(right, arrowLength * 0.5);
    const arrowRight = arrowBase
      .clone()
      .addScaledVector(right, -arrowLength * 0.5);
    const vertices = [
      position,
      target,
      target,
      arrowLeft,
      target,
      arrowRight,
      position,
      left,
      position,
      rightEdge,
    ];
    const attribute = this.line.geometry.getAttribute(
      'position',
    ) as THREE.BufferAttribute;
    vertices.forEach((point, index) =>
      attribute.setXYZ(index, point.x, point.y, point.z),
    );
    attribute.needsUpdate = true;
  }

  refresh(camera: THREE.PerspectiveCamera, viewportHeight: number) {
    if (!this.root.visible) return;
    for (const marker of [this.position, this.target]) {
      const scale = Math.max(
        0.08,
        (camera.position.distanceTo(marker.position) *
          2 *
          Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) *
          9) /
          Math.max(1, viewportHeight),
      );
      marker.scale.setScalar(scale);
      marker.quaternion.copy(camera.quaternion);
    }
  }

  clear() {
    this.root.visible = false;
  }

  dispose() {
    this.root.traverse((object) => {
      if (
        object instanceof THREE.Mesh ||
        object instanceof THREE.LineSegments
      ) {
        object.geometry.dispose();
        const materials = Array.isArray(object.material)
          ? object.material
          : [object.material];
        materials.forEach((material) => material.dispose());
      }
    });
    this.root.removeFromParent();
  }
}
