import { AcApDocManager } from '@mlightcad/cad-simple-viewer';
import { AcTrMTextRenderer } from '@mlightcad/three-renderer';

export async function loadMlightViewer(container, url, progress) {
  const manager = AcApDocManager.createInstance({
    container, autoResize: true, baseUrl: '/', useMainThreadDraw: false,
    webworkerFileUrls: { mtextRender: '/assets/mtext-renderer-worker.js' },
  });
  await AcTrMTextRenderer.getInstance().setDefaultFonts(['arial']);
  progress('Загрузка полного файла');
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Source HTTP ${response.status}`);
  const bytes = await response.arrayBuffer();
  progress(`Разбор ${bytes.byteLength} байт`);
  const success = await manager.openDocument(url.split('/').at(-1), bytes, {
    mode: 8, progressiveRendering: true, openViewMode: 'extents', drawNoPlotLayers: true,
  });
  if (!success) throw new Error('MLightCAD openDocument returned false');
  const view = manager.curView;
  view.activeLayoutBtrId = view.modelSpaceBtrId;
  progress('Ожидание геометрии и текста');
  if (!(await view.waitUntilIdle(180_000))) throw new Error('Geometry did not settle within 180 seconds');
  view.zoomToFitDrawing();
  // The SDK's final fit is polled every 300 ms, after openDocument resolves.
  // Include settled, visible geometry in open-time measurement.
  await new Promise((resolve) => setTimeout(resolve, 1000));
  const camera = () => view.internalCamera;
  const renderer = view.renderer.internalRenderer;
  return {
    render() { view.isDirty = true; },
    pan(x, y) {
      const cam = camera();
      cam.position.x += x * (cam.right - cam.left) / cam.zoom / container.clientWidth;
      cam.position.y += y * (cam.top - cam.bottom) / cam.zoom / container.clientHeight;
      cam.updateMatrixWorld(); view.isDirty = true;
    },
    zoom(factor) { camera().zoom *= factor; camera().updateProjectionMatrix(); view.isDirty = true; },
    fit() { view.zoomToFitDrawing(); },
    stats() {
      return { renderer: { ...renderer.info.render }, memory: { ...renderer.info.memory }, scene: view.stats.summary,
        progress: view.progressiveOpenStats, missed: { ...view.missedData, images: [...view.missedData.images] },
        camera: { x: camera().position.x, y: camera().position.y, zoom: camera().zoom },
        sourceBytes: bytes.byteLength, pixelRatio: renderer.getPixelRatio() };
    },
    dispose() { location.reload(); },
  };
}
