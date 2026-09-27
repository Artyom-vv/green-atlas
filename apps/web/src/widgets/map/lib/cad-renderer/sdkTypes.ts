import type { CadLayerInfo } from './types';

// Structural boundary: the SDK owns Three 0.161; the app owns Three 0.185.
// Renderer/Object3D ownership and instanceof never cross versions. Culling uses
// app Sphere/Vector3 only as center/radius math values, tested with SDK Frustum.
export interface CadCamera {
  left: number;
  right: number;
  bottom: number;
  top: number;
  zoom: number;
  position: { set: (x: number, y: number, z: number) => unknown };
  rotation: { set: (x: number, y: number, z: number) => unknown };
  updateProjectionMatrix: () => void;
  updateMatrixWorld: () => void;
}

export interface CadViewer {
  HasRenderer: () => boolean;
  GetCanvas: () => HTMLCanvasElement;
  GetCamera: () => CadCamera;
  GetOrigin: () => { x: number; y: number };
  GetBounds: () => {
    minX: number;
    minY: number;
    maxX: number;
    maxY: number;
  } | null;
  GetLayers: (nonEmptyOnly?: boolean) => Iterable<CadLayerInfo>;
  GetRenderer: () => {
    info: {
      render: {
        calls: number;
        lines?: number;
        triangles?: number;
        points?: number;
      };
    };
    getContext: () => WebGLRenderingContext | WebGL2RenderingContext;
  } | null;
  GetScene: () => { children: unknown[] };
  SetSize: (width: number, height: number) => void;
  ShowLayers: (visibility: Record<string, boolean>) => void;
  Render: () => void;
  Destroy: () => void;
  Load: (options: {
    url: string;
    fonts: string[];
    workerFactory: () => Worker;
  }) => Promise<void>;
  hasMissingChars?: boolean;
}
