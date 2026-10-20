import { afterEach, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import type { ScenePlantObject } from '@green/api-client';
import { SceneCamera } from './SceneCamera';

afterEach(() => vi.restoreAllMocks());

const distantPosition = new THREE.Vector3(-266.3228765, 5534.5694349, 3305.8614864);
const bounds = new THREE.Box3(
  new THREE.Vector3(-27.675, 0, -20),
  new THREE.Vector3(27.675, 4.5, 20),
);

function distantCamera() {
  vi.spyOn(performance, 'now').mockReturnValue(1_000);
  const canvas = document.createElement('canvas');
  vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue(
    new DOMRect(0, 0, 900, 1_000),
  );
  const navigation = new SceneCamera(canvas, {
    onMove: vi.fn(),
    onClipChange: vi.fn(),
  });
  navigation.setBasis([0, 0], () => 0);
  navigation.camera.position.copy(distantPosition);
  navigation.controls.target.set(0, 0, 0);
  navigation.controls.update();
  navigation.camera.near = 1;
  navigation.camera.far = 51_659;
  navigation.camera.updateProjectionMatrix();
  return navigation;
}

function expectOriginInDepthRange(navigation: SceneCamera) {
  const pose = navigation.exportRenderCamera()!;
  expect(pose.far).toBeGreaterThan(navigation.camera.position.length());
  const projected = new THREE.Vector3().project(navigation.camera);
  expect(projected.z).toBeGreaterThan(-1);
  expect(projected.z).toBeLessThan(1);
}

it('keeps the actual distant pose visible while fitting a view whose final far plane is 321 m', () => {
  const navigation = distantCamera();
  try {
    const initialPosition = navigation.camera.position.toArray();
    navigation.fitBounds(bounds, true);
    expect(navigation.exportRenderCamera()!.position).toEqual(initialPosition);
    expectOriginInDepthRange(navigation);

    navigation.advance(1_340);
    navigation.controls.update();
    expectOriginInDepthRange(navigation);

    expect(navigation.advance(1_680)).toBe(true);
    navigation.controls.update();
    expect(navigation.animating).toBe(false);
    expect(navigation.exportRenderCamera()!.far).toBeCloseTo(321.03);
    expectOriginInDepthRange(navigation);
  } finally {
    navigation.dispose();
  }
});

it.each([false, true])('keeps a distant focus transition renderable, ground level %s', (groundLevel) => {
  const navigation = distantCamera();
  const plant = {
    local_x: 0,
    local_y: 0,
    height_max_m: 4.5,
    canopy_radius_max_m: 0.23,
  } as ScenePlantObject;
  try {
    const initialPosition = navigation.camera.position.toArray();
    navigation.focusObject(plant, groundLevel);
    expect(navigation.exportRenderCamera()!.position).toEqual(initialPosition);
    expectOriginInDepthRange(navigation);

    navigation.advance(1_300);
    navigation.controls.update();
    expectOriginInDepthRange(navigation);

    expect(navigation.advance(1_620)).toBe(true);
    navigation.controls.update();
    expect(navigation.exportRenderCamera()!.far).toBe(120);
    expectOriginInDepthRange(navigation);
  } finally {
    navigation.dispose();
  }
});

it('refreshes the transition clipping for an exported pose without moving that pose', () => {
  const navigation = distantCamera();
  try {
    navigation.fitBounds(bounds, true);
    navigation.camera.position.copy(distantPosition).multiplyScalar(12);
    navigation.camera.lookAt(navigation.controls.target);
    const actualPosition = navigation.camera.position.toArray();
    expect(navigation.camera.far).toBeLessThan(navigation.camera.position.length());
    expectOriginInDepthRange(navigation);
    expect(navigation.exportRenderCamera()!.position).toEqual(actualPosition);

    navigation.fitBounds(bounds, false);
    expect(navigation.animating).toBe(false);
    expect(navigation.exportRenderCamera()!.far).toBeCloseTo(321.03);
    expectOriginInDepthRange(navigation);
  } finally {
    navigation.dispose();
  }
});
