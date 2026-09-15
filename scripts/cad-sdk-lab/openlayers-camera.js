/** Synchronize the SDK's public Three.js camera to an OpenLayers render frame. */
export function renderCadFrame(viewer, frameState, dimensions) {
  const [width, height] = frameState.size;
  if (!width || !height) return;
  if (dimensions[0] !== width || dimensions[1] !== height) {
    viewer.SetSize(width, height);
    dimensions[0] = width;
    dimensions[1] = height;
  }
  const { center, resolution, rotation } = frameState.viewState;
  const origin = viewer.GetOrigin();
  const camera = viewer.GetCamera();
  // GetCamera is the SDK's public Three.js camera API. SetView would invoke
  // the SDK OrbitControls change listener, rendering a second time per pan.
  camera.left = -width * resolution / 2;
  camera.right = width * resolution / 2;
  camera.bottom = -height * resolution / 2;
  camera.top = height * resolution / 2;
  camera.zoom = 1;
  camera.position.set(center[0] - origin.x, center[1] - origin.y, 1);
  camera.rotation.set(0, 0, rotation);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  viewer.Render();
}
