import { afterEach, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import { SceneController } from './SceneController';
import { StreetCameraGuide } from './StreetCameraGuide';

afterEach(() => vi.restoreAllMocks());

function controller() {
  let point: [number, number] = [1010, 2020];
  const guide = new StreetCameraGuide();
  const state = Object.assign(Object.create(SceneController.prototype), {
    canvas: document.createElement('canvas'),
    streetGuide: guide,
    snapshot: { coordinate_origin: [1000, 2000] },
    terrainElevationAt: () => 150,
    sourceCoordinateAt: () => point,
    navigation: {
      camera: new THREE.PerspectiveCamera(42, 1.5),
      mode: 'overview',
      placeStreetCamera: vi.fn(() => true),
    },
    plants: { objectAt: vi.fn(() => 'plant') },
    onSelect: vi.fn(),
    onHover: vi.fn(),
    onMoveTarget: vi.fn(),
    rebuildOutlines: vi.fn(),
    requestRender: vi.fn(),
  });
  return {
    state,
    controller: state as SceneController,
    point: (next: [number, number]) => {
      point = next;
    },
    click: (shiftKey = false) =>
      state.selectStreetPoint(new MouseEvent('click', { shiftKey })),
    dispose: () => guide.dispose(),
  };
}

it('selects two source points with visible guides, but moves the camera only after confirmation', () => {
  const test = controller();
  const placed = vi.fn();
  const changed = vi.fn();
  try {
    test.controller.beginStreetPlacement(placed, changed);
    test.click();
    expect(changed).toHaveBeenLastCalledWith({
      step: 'direction',
      position: [1010, 2020],
      snapped: false,
    });
    expect(test.state.streetGuide.root.visible).toBe(true);
    expect(test.controller.confirmStreetPlacement()).toBe(false);
    test.point([1030, 2024]);
    test.click(true);
    expect(changed).toHaveBeenLastCalledWith(
      expect.objectContaining({ step: 'ready', snapped: true }),
    );
    expect(test.state.navigation.placeStreetCamera).not.toHaveBeenCalled();
    expect(placed).not.toHaveBeenCalled();
    expect(test.state.plants.objectAt).not.toHaveBeenCalled();
    expect(test.state.onMoveTarget).not.toHaveBeenCalled();
    expect(test.controller.confirmStreetPlacement()).toBe(true);
    expect(test.state.navigation.placeStreetCamera).toHaveBeenCalledWith(
      [1010, 2020],
      [1010 + Math.hypot(20, 4), 2020],
    );
    expect(placed).toHaveBeenCalledOnce();
    expect(test.state.streetGuide.root.visible).toBe(false);
    expect(test.state.canvas.style.cursor).toBe('');
  } finally {
    test.dispose();
  }
});

it('cancels and restarts the draft without changing the actual camera or planting selection', () => {
  const test = controller();
  const changed = vi.fn();
  try {
    test.controller.beginStreetPlacement(vi.fn(), changed);
    test.click();
    test.controller.resetStreetPlacement();
    expect(changed).toHaveBeenLastCalledWith({
      step: 'position',
      snapped: false,
    });
    expect(test.state.streetGuide.root.visible).toBe(false);
    test.controller.cancelStreetPlacement();
    expect(test.state.navigation.placeStreetCamera).not.toHaveBeenCalled();
    expect(test.state.onSelect).not.toHaveBeenCalled();
    test.click();
    expect(test.state.onSelect).not.toHaveBeenCalled();
  } finally {
    test.dispose();
  }
});

it('does not place a camera after a navigation drag', () => {
  const test = controller();
  const changed = vi.fn();
  try {
    test.controller.beginStreetPlacement(vi.fn(), changed);
    test.state.pointerDown = { clientX: 20, clientY: 20 };
    test.state.selectStreetPoint(
      new MouseEvent('click', { clientX: 50, clientY: 25 }),
    );
    expect(changed).toHaveBeenCalledTimes(1);
    expect(test.state.plants.objectAt).not.toHaveBeenCalled();
  } finally {
    test.dispose();
  }
});

it('keeps camera and target markers legible as the overview zoom changes', () => {
  const guide = new StreetCameraGuide();
  const camera = new THREE.PerspectiveCamera(42, 1.5);
  try {
    guide.update(new THREE.Vector3(0, 0, 0), new THREE.Vector3(10, 0, 0));
    camera.position.set(0, 100, 0);
    camera.lookAt(0, 0, 0);
    guide.refresh(camera, 800);
    const marker = guide.root.children[0];
    const nearScale = marker.scale.x;
    expect(marker.quaternion.equals(camera.quaternion)).toBe(true);
    camera.position.y = 200;
    guide.refresh(camera, 800);
    expect(marker.scale.x).toBeCloseTo(nearScale * 2);
    const line = guide.root.children[2] as THREE.LineSegments;
    const attribute = line.geometry.getAttribute('position');
    guide.update(new THREE.Vector3(1, 0, 2), new THREE.Vector3(11, 0, 2));
    expect(line.geometry.getAttribute('position')).toBe(attribute);
    expect(attribute.getX(0)).toBe(1);
  } finally {
    guide.dispose();
  }
});
