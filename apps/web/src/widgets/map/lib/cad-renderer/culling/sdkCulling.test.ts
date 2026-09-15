import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';
import { prepareCadFrustumCulling } from './prepareCadFrustumCulling';

// Frustum/renderable objects come from the SDK's Three0.161. Only the helper's
// Sphere/Vector3 math values cross versions through Three's center/radius API.
const sdkRequire = createRequire(
  createRequire(import.meta.url).resolve('dxf-viewer'),
);
const sdk = sdkRequire('three') as typeof import('three');

function geometry(position = [-2, -1, 2, -1, 2, 1, -2, 1]) {
  return new sdk.BufferGeometry().setAttribute(
    'position',
    new sdk.BufferAttribute(new Float32Array(position), 2),
  );
}

function affine(rows: number[]) {
  const result = new sdk.InstancedBufferGeometry();
  result.setAttribute('position', geometry().getAttribute('position'));
  const data = new sdk.InstancedInterleavedBuffer(new Float32Array(rows), 6);
  result.setAttribute(
    'instanceTransform0',
    new sdk.InterleavedBufferAttribute(data, 3, 0),
  );
  result.setAttribute(
    'instanceTransform1',
    new sdk.InterleavedBufferAttribute(data, 3, 3),
  );
  return result;
}

function scene(...objects: import('three').Object3D[]) {
  const result = new sdk.Scene();
  result.add(...objects);
  result.updateMatrixWorld(true);
  return result;
}

function frustum(x = 0, y = 0, halfSize = 5, rotation = 0) {
  const camera = new sdk.OrthographicCamera(
    -halfSize,
    halfSize,
    halfSize,
    -halfSize,
    0.1,
    2,
  );
  camera.position.set(x, y, 1);
  camera.rotation.z = rotation;
  camera.updateMatrixWorld(true);
  return new sdk.Frustum().setFromProjectionMatrix(
    new sdk.Matrix4().multiplyMatrices(
      camera.projectionMatrix,
      camera.matrixWorldInverse,
    ),
  );
}

describe('SDK native CAD frustum bounds', () => {
  it('provides valid 2D bounds to SDK Frustum without changing geometry or visibility', () => {
    const input = geometry([-12, -11, -8, -11, -8, -9]);
    const mesh = new sdk.Mesh(input);
    mesh.visible = false;
    const position = input.getAttribute('position');
    const dispose = vi.spyOn(input, 'dispose');
    const prepared = prepareCadFrustumCulling(scene(mesh));
    expect(prepared.summary.enabledObjects).toBe(1);
    expect(mesh.frustumCulled).toBe(true);
    expect(mesh.visible).toBe(false);
    expect(input.getAttribute('position')).toBe(position);
    expect(input.boundingSphere?.center.z).toBe(0);
    expect(Number.isFinite(input.boundingSphere?.radius)).toBe(true);
    expect(frustum(-10, -10).intersectsObject(mesh)).toBe(true);
    expect(frustum(1000, 1000).intersectsObject(mesh)).toBe(false);
    expect(dispose).not.toHaveBeenCalled();
  });

  it('contains every negative/nonuniform/rotated affine instance, including Infinity default counts', () => {
    const rows = [
      -2, 0, -100, 0, 0.5, -20, 0, -3, 100, 2, 0, 50, 0.5, 0.25, 4, -0.75, -2,
      6,
    ];
    const input = affine(rows);
    expect(input.instanceCount).toBe(Infinity);
    const mesh = new sdk.Mesh(input);
    prepareCadFrustumCulling(scene(mesh));
    for (let instance = 0; instance < rows.length; instance += 6) {
      const [a, b, tx, c, d, ty] = rows.slice(instance, instance + 6);
      for (const x of [-2, 2])
        for (const y of [-1, 1]) {
          const point = new sdk.Vector3(
            a * x + b * y + tx,
            c * x + d * y + ty,
            0,
          );
          expect(input.boundingSphere?.containsPoint(point)).toBe(true);
          expect(
            frustum(point.x, point.y, 0.01, Math.PI / 3).intersectsObject(mesh),
          ).toBe(true);
        }
    }
    expect(frustum(10000, 10000).intersectsObject(mesh)).toBe(false);
  });

  it('includes point-instance translations of line shapes while leaving raster POINTS unculled', () => {
    const input = new sdk.InstancedBufferGeometry();
    input.setAttribute('position', geometry().getAttribute('position'));
    input.setAttribute(
      'instanceTransform',
      new sdk.InstancedBufferAttribute(
        new Float32Array([-100, -50, 200, 40]),
        2,
      ),
    );
    const line = new sdk.LineSegments(input);
    const dots = new sdk.Points(geometry([900, 900]));
    const prepared = prepareCadFrustumCulling(scene(line, dots));
    expect(line.frustumCulled).toBe(false);
    prepared.updateForResolution(0.1);
    expect(line.frustumCulled).toBe(true);
    expect(prepared.summary.pointObjectsUnculled).toBe(1);
    expect(dots.frustumCulled).toBe(false);
    expect(
      input.boundingSphere?.containsPoint(new sdk.Vector3(-102, -51, 0)),
    ).toBe(true);
    expect(
      input.boundingSphere?.containsPoint(new sdk.Vector3(202, 41, 0)),
    ).toBe(true);
    expect(frustum(10000, 10000).intersectsObject(line)).toBe(false);
  });

  it('pads raster line edges per resolution and compensates negative/nonuniform world scale', () => {
    const input = geometry([201, 0, 201, 0.01]);
    const line = new sdk.LineSegments(
      input,
      new sdk.LineBasicMaterial({ linewidth: 2 }),
    );
    line.scale.set(-0.25, 0.1, 0.2);
    line.rotation.z = Math.PI / 4;
    const prepared = prepareCadFrustumCulling(scene(line));
    const view = frustum(0, 0, 50, Math.PI / 4);
    expect(view.intersectsObject(line)).toBe(false);
    prepared.updateForResolution(1);
    expect(view.intersectsObject(line)).toBe(true);
    const expanded = input.boundingSphere!.radius;
    prepared.updateForResolution(100);
    expect(input.boundingSphere!.radius).toBeGreaterThan(expanded);
    prepared.updateForResolution(0.001);
    expect(view.intersectsObject(line)).toBe(false);
    prepared.updateForResolution(Infinity);
    expect(line.frustumCulled).toBe(false);
  });

  it('reads shared vertex buffers and per-geometry instance bounds once', () => {
    const first = affine([1, 0, 10, 0, 1, 20]);
    const second = affine([1, 0, 100, 0, 1, 200]);
    second.setAttribute('position', first.getAttribute('position'));
    const prepared = prepareCadFrustumCulling(
      scene(new sdk.Mesh(first), new sdk.Mesh(first), new sdk.Mesh(second)),
    );
    expect(prepared.summary).toMatchObject({
      boundedGeometries: 2,
      positionVerticesRead: 4,
      transformsRead: 2,
      enabledObjects: 3,
    });
  });

  it('keeps Float32 shader multiply-add rounding inside conservative bounds', () => {
    const input = affine([100.1, 0.2, -100_300_000, -0.3, 50.2, -49_900_000]);
    input.setAttribute(
      'position',
      geometry([1_000_000, 1_000_000, 1_000_001, 1_000_001]).getAttribute(
        'position',
      ),
    );
    prepareCadFrustumCulling(scene(new sdk.Mesh(input)));
    const p = input.getAttribute('position');
    const xRow = input.getAttribute('instanceTransform0');
    const yRow = input.getAttribute('instanceTransform1');
    const fp = Math.fround;
    for (let index = 0; index < p.count; index += 1) {
      const x = fp(
        fp(
          fp(xRow.getX(0) * p.getX(index)) + fp(xRow.getY(0) * p.getY(index)),
        ) + xRow.getZ(0),
      );
      const y = fp(
        fp(
          fp(yRow.getX(0) * p.getX(index)) + fp(yRow.getY(0) * p.getY(index)),
        ) + yRow.getZ(0),
      );
      expect(
        input.boundingSphere?.containsPoint(new sdk.Vector3(x, y, 0)),
      ).toBe(true);
    }
  });

  it('uses the largest raster margin for geometry shared by differently scaled lines', () => {
    const input = geometry([100, 0, 101, 0]);
    const first = new sdk.LineSegments(input);
    const second = new sdk.LineSegments(input);
    first.scale.setScalar(10);
    second.scale.setScalar(0.1);
    const prepared = prepareCadFrustumCulling(scene(first, second));
    const baseRadius = input.boundingSphere!.radius;
    prepared.updateForResolution(2);
    expect(input.boundingSphere!.radius - baseRadius).toBeCloseTo(20);
    expect(prepared.summary.boundedGeometries).toBe(1);
  });

  it.each(['empty', 'nan', 'missing-row', 'unknown-instance', 'zero-scale'])(
    'leaves %s geometry unculled instead of inventing unsafe bounds',
    (kind) => {
      const input =
        kind === 'missing-row' || kind === 'unknown-instance'
          ? new sdk.InstancedBufferGeometry()
          : geometry(
              kind === 'empty' ? [] : kind === 'nan' ? [NaN, 0] : [0, 0],
            );
      if (kind === 'missing-row' || kind === 'unknown-instance')
        input.setAttribute('position', geometry().getAttribute('position'));
      if (kind === 'missing-row')
        input.setAttribute(
          'instanceTransform0',
          new sdk.InstancedBufferAttribute(new Float32Array([1, 0, 0]), 3),
        );
      const mesh = new sdk.Mesh(input);
      if (kind === 'zero-scale') mesh.scale.set(0, 0, 0);
      const prepared = prepareCadFrustumCulling(scene(mesh));
      expect(mesh.frustumCulled).toBe(false);
      expect(prepared.summary.unsafeObjectsUnculled).toBe(1);
    },
  );

  it('keeps unsupported world shear unculled, including a line matrix changed later', () => {
    const mesh = new sdk.Mesh(geometry());
    mesh.matrixAutoUpdate = false;
    mesh.matrix.makeShear(1, 0, 0, 0, 0, 0);
    const line = new sdk.LineSegments(geometry());
    const prepared = prepareCadFrustumCulling(scene(mesh, line));
    expect(mesh.frustumCulled).toBe(false);
    prepared.updateForResolution(1);
    expect(line.frustumCulled).toBe(true);
    line.matrixWorld.makeShear(1, 0, 0, 0, 0, 0);
    prepared.updateForResolution(1);
    expect(line.frustumCulled).toBe(false);
  });
});
