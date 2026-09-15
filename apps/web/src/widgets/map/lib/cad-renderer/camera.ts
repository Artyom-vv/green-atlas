import type { CadViewer } from './sdkTypes';
import type { CadSourceReadyInfo } from './types';

export interface CadFrame {
  size: number[];
  viewState: { center: number[]; resolution: number; rotation: number };
}

export function validateUnitScale(scale: number): void {
  if (!Number.isFinite(scale) || scale <= 0) {
    throw new Error('Масштаб единиц CAD должен быть положительным числом.');
  }
}

export function renderCadFrame(
  viewer: CadViewer,
  frame: CadFrame,
  scale: number,
  dimensions: number[],
): void {
  const [width, height] = frame.size;
  if (!width || !height) return;
  if (dimensions[0] !== width || dimensions[1] !== height) {
    viewer.SetSize(width, height);
    dimensions[0] = width;
    dimensions[1] = height;
  }
  const { center, resolution, rotation } = frame.viewState;
  const origin = viewer.GetOrigin();
  const camera = viewer.GetCamera();
  const sourceResolution = resolution / scale;
  camera.left = (-width * sourceResolution) / 2;
  camera.right = (width * sourceResolution) / 2;
  camera.bottom = (-height * sourceResolution) / 2;
  camera.top = (height * sourceResolution) / 2;
  camera.zoom = 1;
  camera.position.set(
    center[0] / scale - origin.x,
    center[1] / scale - origin.y,
    1,
  );
  camera.rotation.set(0, 0, rotation);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  viewer.Render();
}

export function getReadyInfo(
  viewer: CadViewer,
  scale: number,
): CadSourceReadyInfo {
  const bounds = viewer.GetBounds();
  const origin = viewer.GetOrigin();
  if (!bounds) throw new Error('В CAD-источнике нет границ отображения.');
  const info: CadSourceReadyInfo = {
    boundsM: [
      bounds.minX * scale,
      bounds.minY * scale,
      bounds.maxX * scale,
      bounds.maxY * scale,
    ],
    originM: [origin.x * scale, origin.y * scale],
    layers: [...viewer.GetLayers()],
    missingCharacters: Boolean(viewer.hasMissingChars),
    renderCalls: viewer.GetRenderer()?.info.render.calls ?? 0,
    sceneObjects: viewer.GetScene().children.length,
  };
  if (
    ![...info.boundsM, ...info.originM].every(Number.isFinite) ||
    info.boundsM[0] > info.boundsM[2] ||
    info.boundsM[1] > info.boundsM[3]
  ) {
    throw new Error('Некорректные координаты CAD-источника.');
  }
  return info;
}
