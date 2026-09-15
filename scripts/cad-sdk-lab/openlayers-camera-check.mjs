import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { create, compose, apply } from 'ol/transform.js';
import { renderCadFrame } from './openlayers-camera.js';

const require = createRequire(import.meta.resolve('dxf-viewer'));
const { OrthographicCamera, Vector3 } = require('three');
const origin = { x: 2100000.125, y: -5700000.875 };
const camera = new OrthographicCamera(-1, 1, 1, -1, 0.1, 2);
let renders = 0;
let resizes = 0;
const viewer = {
  GetCamera: () => camera, GetOrigin: () => origin,
  SetSize() { resizes += 1; }, Render() { renders += 1; },
};
const dimensions = [0, 0];
const cases = [];
for (const [width, height, resolution, rotation] of [
  [1024, 600, 5, 0], [1024, 600, 0.001, 0],
  [640, 900, 1.5, 0], [640, 900, 1.5, Math.PI / 3],
]) {
  const center = [origin.x + 63.5, origin.y - 91.25];
  renderCadFrame(viewer, { size: [width, height],
    viewState: { center, resolution, rotation } }, dimensions);
  const transform = compose(create(), width / 2, height / 2,
    1 / resolution, -1 / resolution, -rotation, -center[0], -center[1]);
  let maxErrorCssPixels = 0;
  for (const [dx, dy] of [[0, 0], [-30, 50], [70, -22]]) {
    const xy = [center[0] + dx * resolution, center[1] + dy * resolution];
    const olPixel = apply(transform, [...xy]);
    const ndc = new Vector3(xy[0] - origin.x, xy[1] - origin.y, 0).project(camera);
    const cadPixel = [(ndc.x + 1) * width / 2, (1 - ndc.y) * height / 2];
    const error = Math.hypot(cadPixel[0] - olPixel[0], cadPixel[1] - olPixel[1]);
    maxErrorCssPixels = Math.max(maxErrorCssPixels, error);
    assert.ok(error < 0.00001, `Coordinate mismatch: ${error}px`);
  }
  cases.push({ width, height, resolution, rotation, maxErrorCssPixels });
}
assert.equal(renders, 4, 'One explicit CAD render per OL frame');
assert.equal(resizes, 2, 'Only changed CSS dimensions resize the SDK');
console.log(JSON.stringify({ status: 'passed', source: 'Real OL transform + SDK Three camera',
  origin, renders, resizes, cases }, null, 2));
