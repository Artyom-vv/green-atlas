import { DxfViewer } from "dxf-viewer";

const FONT_URL = "/fonts/fallback.ttf";

export async function loadDxfViewer(container, url, onProgress = () => {}) {
  const startedAt = performance.now();
  const messages = [];
  let workerScene = null;
  let disposed = false;
  let rejectWorker;
  const workerFailure = new Promise((_, reject) => {
    rejectWorker = reject;
  });
  const viewer = new DxfViewer(container, {
    autoResize: true,
    clearColor: DxfViewer.DefaultOptions.clearColor.clone().setHex(0xf7f8f9),
    // Retaining the full parser object would structured-clone it back to the
    // main thread. This test retains layers and GPU buffers, not a second DXF.
    retainParsedDxf: false,
    fileEncoding: "utf-8",
  });
  if (!viewer.HasRenderer()) {
    throw new Error("dxf-viewer could not create a WebGL renderer");
  }
  viewer.Subscribe("message", (event) => messages.push(event.detail));

  try {
    await Promise.race([
      viewer.Load({
        url,
        fonts: [new URL(FONT_URL, window.location.href).href],
        progressCbk: (phase, loaded, total) =>
          onProgress({ phase, loaded, total, elapsedMs: performance.now() - startedAt }),
        workerFactory: () => {
          const worker = new Worker(new URL("./dxf-worker.js", import.meta.url), {
            type: "module",
          });
          // Observe the SDK's response; do not change its preparation or buffers.
          worker.addEventListener("message", ({ data }) => {
            if (data.signature !== "DxfWorkerMsg" || data.type !== "LOAD") return;
            const scene = data.data?.scene;
            if (!scene) return;
            workerScene = {
              batches: scene.batches.length,
              verticesBytes: scene.vertices.byteLength,
              indicesBytes: scene.indices.byteLength,
              transformsBytes: scene.transforms.byteLength,
              instancedTransforms: scene.transforms.byteLength / (6 * 4),
            };
          });
          // SDK 1.0.44's fatal-worker rejection does not enumerate Map.values().
          // Bound the lab load to the actual worker error instead of hanging.
          worker.addEventListener("error", (event) =>
            rejectWorker(new Error(event.message || "DXF preparation worker failed")),
          );
          worker.addEventListener("messageerror", () =>
            rejectWorker(new Error("DXF preparation worker message could not be decoded")),
          );
          return worker;
        },
      }),
      workerFailure,
    ]);
  } catch (error) {
    const canvas = viewer.GetCanvas();
    viewer.Destroy();
    canvas.remove();
    throw error;
  }

  const loadMs = performance.now() - startedAt;
  const renderer = viewer.GetRenderer();
  const gl = renderer.getContext();
  const debugInfo = gl.getExtension("WEBGL_debug_renderer_info");
  const gpu = debugInfo ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) : null;
  const layers = Array.from(viewer.GetLayers());

  function setCamera(center, width, zoom) {
    viewer.SetView(center, width, zoom);
    // SetView updates the SDK controls, which render on their change event.
    // render() remains available for the harness's explicit frame measurement.
  }

  return {
    getViewer: () => viewer,
    render() {
      if (!disposed) viewer.Render();
    },
    pan(xPixels, yPixels) {
      const camera = viewer.GetCamera();
      const canvas = viewer.GetCanvas();
      const width = camera.right - camera.left;
      const height = camera.top - camera.bottom;
      setCamera({
        x: camera.position.x - xPixels * width / camera.zoom / canvas.clientWidth,
        y: camera.position.y + yPixels * height / camera.zoom / canvas.clientHeight,
      }, width, camera.zoom);
    },
    zoom(factor) {
      if (!Number.isFinite(factor) || factor <= 0) throw new Error("Invalid zoom factor");
      const camera = viewer.GetCamera();
      setCamera(camera.position, camera.right - camera.left, camera.zoom * factor);
    },
    fit() {
      const bounds = viewer.GetBounds();
      const origin = viewer.GetOrigin();
      if (!bounds || !origin) return;
      viewer.FitView(bounds.minX - origin.x, bounds.maxX - origin.x,
        bounds.minY - origin.y, bounds.maxY - origin.y);
      viewer.Render();
    },
    frame([minX, minY, maxX, maxY]) {
      const origin = viewer.GetOrigin();
      if (!origin || ![minX, minY, maxX, maxY].every(Number.isFinite) ||
        minX >= maxX || minY >= maxY) throw new Error("Invalid source extent");
      viewer.FitView(minX - origin.x, maxX - origin.x,
        minY - origin.y, maxY - origin.y);
      viewer.Render();
    },
    stats() {
      const camera = viewer.GetCamera();
      const origin = viewer.GetOrigin();
      const scene = viewer.GetScene();
      return {
        engine: "dxf-viewer@1.0.44",
        loadMs,
        gpu,
        devicePixelRatio: renderer.getPixelRatio(),
        canvas: { width: viewer.GetCanvas().width, height: viewer.GetCanvas().height },
        render: { ...renderer.info.render },
        memory: { ...renderer.info.memory },
        programs: renderer.info.programs?.length ?? null,
        layers,
        layerCount: layers.length,
        nonEmptyLayerCount: Array.from(viewer.GetLayers(true)).length,
        sourceEntityCount: null,
        sourceEntityCountReason: "Not exposed without retaining/cloning the entire parser result",
        sceneObjects: scene.children.length,
        workerScene,
        origin,
        bounds: viewer.GetBounds(),
        camera: {
          position: camera.position.toArray(),
          zoom: camera.zoom,
          left: camera.left, right: camera.right,
          top: camera.top, bottom: camera.bottom,
          sourceCenter: origin ? {
            x: camera.position.x + origin.x, y: camera.position.y + origin.y,
          } : null,
        },
        font: { url: FONT_URL, missingCharacters: Boolean(viewer.hasMissingChars) },
        messages: [...messages],
        parsedDxfRetained: false,
        culling: "SDK sets frustumCulled=false on every source render batch",
        lod: "No viewport tile/LOD layer added by this adapter",
      };
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      const canvas = viewer.GetCanvas();
      viewer.Destroy();
      canvas.remove();
    },
  };
}
