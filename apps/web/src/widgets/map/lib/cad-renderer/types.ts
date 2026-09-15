import type Layer from 'ol/layer/Layer';
import type { CadAppearanceMode, CadLayerRoles } from '../../model/cadSource';

export interface CadLayerInfo {
  name: string;
  displayName: string;
  color: number;
}

export type CadBoundsM = [number, number, number, number];

export interface CadSourceReadyInfo {
  boundsM: CadBoundsM;
  originM: [number, number];
  layers: CadLayerInfo[];
  missingCharacters: boolean;
  renderCalls: number;
  sceneObjects: number;
}

export interface CadSourceLayerOptions {
  assetUrl: string;
  unitScaleToM: number;
  fileEncoding?: string;
  signal?: AbortSignal;
  onReady?: (info: CadSourceReadyInfo) => void;
  onError: (error: Error) => void;
}

export interface CadSourceLayerController {
  layer: Layer;
  ready: Promise<CadSourceReadyInfo | null>;
  setVisible: (visible: boolean) => void;
  setLayerVisibility: (hiddenLayerNames: ReadonlySet<string>) => void;
  setAppearance: (mode: CadAppearanceMode, layerRoles: CadLayerRoles) => void;
  dispose: () => void;
}
