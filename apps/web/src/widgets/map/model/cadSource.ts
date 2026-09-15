import type { LayerKind } from '@green/api-client';

export type CadAppearanceMode = 'design' | 'cad';
export type CadLayerRoles = Readonly<Record<string, LayerKind>>;

export interface MapCadSource {
  url: string;
  sha256: string;
  unitScaleToM: number;
  fileEncoding?: string;
  /** Native DXF replaces the source visual; computed/editable overlays remain. */
  visualOwnership?: 'source' | 'all';
  layerRoles?: CadLayerRoles;
}

export interface CadRenderState {
  status: 'loading' | 'ready' | 'error';
  message?: string;
}
