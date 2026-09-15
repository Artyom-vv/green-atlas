function numericValue(value: string | undefined) {
  if (!value) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function probeContext(target: HTMLElement) {
  return {
    sourceSha256: target.dataset.cadSourceSha256 ?? null,
    cadRendererState: target.dataset.cadRendererState ?? null,
    geometryReady: target.dataset.geometryReady === 'true',
    geometryRevision: numericValue(target.dataset.geometryRevision),
    deliveredFeatures: numericValue(target.dataset.geometryDeliveryFeatures),
    dimensionsCssPx: [target.clientWidth, target.clientHeight],
    devicePixelRatio: window.devicePixelRatio,
    rendererCallsAtLoad: numericValue(target.dataset.cadRenderCalls),
    loadMs: numericValue(target.dataset.cadLoadMs),
  };
}

export function probeView(target: HTMLElement) {
  return {
    center: [
      numericValue(target.dataset.planViewCenterX),
      numericValue(target.dataset.planViewCenterY),
    ],
    resolution: numericValue(target.dataset.planViewResolution),
    rotation: numericValue(target.dataset.planViewRotation),
  };
}

export function createProbeSurface(target: HTMLElement) {
  const host = document.createElement('div');
  host.dataset.mapPerformanceProbe = '';
  Object.assign(host.style, {
    position: 'absolute',
    bottom: '48px',
    right: '8px',
    zIndex: '1000',
    maxWidth: 'min(480px, 90%)',
    background: '#fff',
    color: '#171717',
    padding: '8px',
    border: '1px solid #aaa',
    borderRadius: '4px',
  });
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = 'Замер карты · 10 с';
  const gestureButton = document.createElement('button');
  gestureButton.type = 'button';
  gestureButton.textContent = 'Записать мои жесты · 10 с';
  gestureButton.style.marginLeft = '8px';
  const output = document.createElement('pre');
  output.dataset.mapPerformanceResult = '';
  Object.assign(output.style, {
    fontSize: '11px',
    maxHeight: '240px',
    overflow: 'auto',
    margin: '0',
  });
  host.append(button, gestureButton, output);
  target.append(host);
  return { host, button, gestureButton, output };
}
