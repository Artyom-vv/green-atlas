import Layer from 'ol/layer/Layer';
import { CadSourceSession } from './CadSourceSession';
import { validateUnitScale } from './camera';
import type { CadSourceLayerController, CadSourceLayerOptions } from './types';

export function createCadSourceLayer(
  options: CadSourceLayerOptions,
): CadSourceLayerController {
  validateUnitScale(options.unitScaleToM);
  const host = document.createElement('div');
  host.className = 'ol-layer';
  Object.assign(host.style, {
    position: 'absolute',
    inset: '0',
    width: '100%',
    height: '100%',
    pointerEvents: 'none',
  });
  const layer: Layer = new Layer({
    zIndex: -10,
    render: (frame) => {
      host.style.opacity = String(layer.getOpacity());
      session.render(frame);
      return host;
    },
  });
  const session = new CadSourceSession(host, options, () => layer.changed());
  const dispose = () => {
    options.signal?.removeEventListener('abort', dispose);
    layer.setVisible(false);
    session.dispose();
  };
  if (options.signal?.aborted) dispose();
  else options.signal?.addEventListener('abort', dispose, { once: true });
  return {
    layer,
    ready: session.load(),
    dispose,
    setVisible: (visible) => layer.setVisible(visible),
    setLayerVisibility: (hiddenNames) => session.setHiddenLayers(hiddenNames),
    setAppearance: (mode, roles) => session.setAppearance(mode, roles),
  };
}
