import type Map from 'ol/Map';
import ImageLayer from 'ol/layer/Image';
import ImageCanvasSource from 'ol/source/ImageCanvas';
import { unByKey } from 'ol/Observable';
import type { EventsKey } from 'ol/events';
import {
  cadGeometryDisplay,
  type CadGeometryLayers,
} from './cadGeometryDisplay';
import { createOverviewCanvas } from './sourceOverviewCanvas';
import type { CadAppearanceMode } from '../../../model/cadSource';

/** Один слой хранит прежний кадр того же снимка до отрисовки нового источника */
export function createSourceOverviewDisplay(
  map: Map,
  target: HTMLDivElement | null,
  layers: CadGeometryLayers | null,
) {
  const display = cadGeometryDisplay(layers);
  const layer = new ImageLayer<ImageCanvasSource>({ zIndex: 0.5 });
  let source: ImageCanvasSource | undefined;
  let appearance: string | undefined;
  let rendered: EventsKey | undefined;
  let generation = 0;
  let added = false;
  return {
    map,
    update(
      markup: string,
      mode: CadAppearanceMode,
      appearanceKey: string,
      count: number,
    ) {
      const drawing = createOverviewCanvas(markup, mode);
      const current = ++generation;
      let painted = false;
      const next = new ImageCanvasSource({
        projection: map.getView().getProjection(),
        ratio: 1.5,
        canvasFunction: (extent, resolution, pixelRatio, size) => {
          const canvas = drawing.render(
            [size[0] / pixelRatio, size[1] / pixelRatio],
            [
              1 / resolution,
              0,
              0,
              -1 / resolution,
              -extent[0] / resolution,
              extent[3] / resolution,
            ],
            pixelRatio,
          );
          painted = true;
          return canvas;
        },
      });
      if (rendered) unByKey(rendered);
      // Скрытые слои и смену оформления нельзя показывать прежним кадром.
      if (appearance !== undefined && appearance !== appearanceKey) {
        display.restore();
        layer.clearRenderer();
        if (target) delete target.dataset.sourceOverviewFeatures;
      }
      appearance = appearanceKey;
      rendered = layer.on('postrender', () => {
        if (!painted || generation !== current) return;
        display.ready('source');
        if (target) target.dataset.sourceOverviewFeatures = String(count);
        if (rendered) unByKey(rendered);
        rendered = undefined;
      });
      // setSource сохраняет bitmap renderer: жест не ждёт первого кадра next.
      layer.setSource(next);
      source?.dispose();
      source = next;
      if (!added) {
        added = true;
        map.addLayer(layer);
      }
      map.renderSync();
    },
    dispose() {
      generation += 1;
      if (rendered) unByKey(rendered);
      rendered = undefined;
      if (added) map.removeLayer(layer);
      display.restore();
      layer.dispose();
      source?.dispose();
      source = undefined;
      if (target) {
        delete target.dataset.sourceOverviewFeatures;
        delete target.dataset.sourceOverviewError;
      }
    },
  };
}
