import { expect, it, vi } from 'vitest';
import * as THREE from 'three';
import { SceneCamera } from './SceneCamera';

function camera() {
  const canvas = document.createElement('canvas');
  vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue(
    new DOMRect(0, 0, 900, 600),
  );
  const navigation = new SceneCamera(canvas, {
    onMove: vi.fn(),
    onClipChange: vi.fn(),
  });
  navigation.setBasis([1000, 2000], () => 150);
  return { canvas, navigation };
}

it('places a street camera at the clicked CAD point and terrain plus eye height', () => {
  const { canvas, navigation } = camera();
  try {
    expect(navigation.placeStreetCamera([1010, 2020])).toBe(true);
    const pose = navigation.exportRenderCamera()!;
    expect(pose.position[0]).toBeCloseTo(10, 8);
    expect(pose.position[1]).toBeCloseTo(151.7, 8);
    expect(pose.position[2]).toBeCloseTo(-20, 8);
    expect(pose.aspect).toBe(1.5);
    expect(navigation.mode).toBe('ground');
    const published = JSON.parse(
      canvas.getAttribute('data-scene-render-camera')!,
    );
    expect(published).toEqual(pose);
  } finally {
    navigation.dispose();
  }
});

it('keeps street eye height and look direction after a vertical orbit', () => {
  const { navigation } = camera();
  try {
    navigation.placeStreetCamera([1010, 2020]);
    navigation.camera.position.set(10, 170, -20);
    navigation.controls.target.set(5, 185, -38);
    navigation.camera.lookAt(navigation.controls.target);
    const direction = navigation.controls.target
      .clone()
      .sub(navigation.camera.position)
      .normalize();
    navigation.constrainStreetEyeHeight();
    expect(navigation.camera.position.y).toBeCloseTo(151.7);
    expect(
      navigation.controls.target
        .clone()
        .sub(navigation.camera.position)
        .normalize()
        .distanceTo(direction),
    ).toBeLessThan(1e-10);
    navigation.mode = 'overview';
    navigation.fitBounds(
      new THREE.Box3(
        new THREE.Vector3(0, 150, 0),
        new THREE.Vector3(20, 170, 30),
      ),
      false,
    );
    expect(navigation.controls.maxPolarAngle).toBeCloseTo(Math.PI / 2.1);
    expect(navigation.controls.minPolarAngle).toBe(0);
  } finally {
    navigation.dispose();
  }
});

it('cannot put a street camera into an unbound source', () => {
  const navigation = new SceneCamera(document.createElement('canvas'), {
    onMove: vi.fn(),
    onClipChange: vi.fn(),
  });
  try {
    expect(navigation.placeStreetCamera([10, 20])).toBe(false);
  } finally {
    navigation.dispose();
  }
});

it('aims at the selected source direction with a level horizon', () => {
  const { navigation } = camera();
  try {
    expect(navigation.placeStreetCamera([1010, 2020], [1030, 2020])).toBe(true);
    const direction = navigation.camera.getWorldDirection(new THREE.Vector3());
    expect(direction.x).toBeCloseTo(1);
    expect(direction.y).toBeCloseTo(0);
    expect(direction.z).toBeCloseTo(0);
    const pose = navigation.exportRenderCamera()!;
    expect(pose.position).toEqual([10, 151.7, -20]);
  } finally {
    navigation.dispose();
  }
});

it('levels the street horizon without moving the selected position or heading', () => {
  const { navigation } = camera();
  try {
    navigation.placeStreetCamera([1010, 2020], [1010, 2000]);
    navigation.controls.target.y += 8;
    navigation.camera.lookAt(navigation.controls.target);
    expect(navigation.levelStreetHorizon()).toBe(true);
    const direction = navigation.camera.getWorldDirection(new THREE.Vector3());
    expect(direction.y).toBeCloseTo(0);
    expect(direction.z).toBeCloseTo(1);
    expect(
      navigation.camera.position.distanceTo(new THREE.Vector3(10, 151.7, -20)),
    ).toBeLessThan(1e-10);
    navigation.mode = 'overview';
    expect(navigation.levelStreetHorizon()).toBe(false);
  } finally {
    navigation.dispose();
  }
});

it('rejects invalid street directions without changing camera pose', () => {
  const { navigation } = camera();
  try {
    const before = navigation.exportRenderCamera();
    expect(navigation.placeStreetCamera([1010, 2020], [1010.1, 2020])).toBe(
      false,
    );
    expect(navigation.placeStreetCamera([NaN, 2020])).toBe(false);
    expect(navigation.exportRenderCamera()).toEqual(before);
  } finally {
    navigation.dispose();
  }
});
