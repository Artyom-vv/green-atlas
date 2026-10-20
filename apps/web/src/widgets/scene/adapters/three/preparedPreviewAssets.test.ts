import { afterEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import {
  assertEmbeddedPreparedGlb,
  disposePreparedPrototype,
  extractPreparedPrototype,
  preparedAssetUrl,
  preparedPreviewAssetLoader,
  type PreparedPreviewAsset,
} from './preparedPreviewAssets';

const asset: PreparedPreviewAsset = {
  id: 'frozen-model',
  kind: 'model',
  lod: 'near',
  url: '/api/projects/project/scene-render-preparations/prep/assets/model',
  sha256: '0'.repeat(64),
};

function glb(document: unknown) {
  const encoded = new TextEncoder().encode(JSON.stringify(document));
  const length = Math.ceil(encoded.length / 4) * 4;
  const buffer = new ArrayBuffer(20 + length);
  const view = new DataView(buffer);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, buffer.byteLength, true);
  view.setUint32(12, length, true);
  view.setUint32(16, 0x4e4f534a, true);
  const json = new Uint8Array(buffer, 20);
  json.fill(32);
  json.set(encoded);
  return buffer;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('private frozen preview resources', () => {
  it('uses the configured local API and accepts same-origin installed assets', () => {
    vi.stubEnv('VITE_API_URL', 'http://127.0.0.1:8039');
    expect(preparedAssetUrl(asset.url)).toBe(
      `http://127.0.0.1:8039${asset.url}`,
    );
    expect(() => preparedAssetUrl('https://example.org/model.glb')).toThrow(
      'локальному API',
    );
    expect(() => preparedAssetUrl('/assets/plant-models/model.glb')).toThrow(
      'локальному API',
    );
    vi.stubEnv('VITE_API_URL', '');
    expect(preparedAssetUrl(asset.url)).toBe(
      `${window.location.origin}${asset.url}`,
    );
  });

  it('rejects external texture and buffer references before GLTFLoader can fetch them', () => {
    expect(() =>
      assertEmbeddedPreparedGlb(
        glb({ buffers: [{ byteLength: 4 }], images: [{ bufferView: 0 }] }),
      ),
    ).not.toThrow();
    for (const document of [
      { images: [{ uri: 'https://example.org/private-leaf.png' }] },
      { buffers: [{ uri: '../unfrozen.bin' }] },
    ])
      expect(() => assertEmbeddedPreparedGlb(glb(document))).toThrow(
        'внешний ресурс',
      );
    expect(() => assertEmbeddedPreparedGlb(new ArrayBuffer(4))).toThrow('GLB');
  });

  it('checks declared bytes and SHA before parsing a changed model', async () => {
    const bytes = glb({ asset: { version: '2.0' } });
    const fetch = vi
      .fn()
      .mockResolvedValue({ ok: true, arrayBuffer: async () => bytes });
    const digest = vi.fn().mockResolvedValue(new Uint8Array(32).buffer);
    vi.stubGlobal('fetch', fetch);
    vi.stubGlobal('crypto', { subtle: { digest } });
    await expect(
      preparedPreviewAssetLoader.model(
        { ...asset, size_bytes: bytes.byteLength + 1 },
        new AbortController().signal,
      ),
    ).rejects.toThrow('Размер');
    expect(digest).not.toHaveBeenCalled();
    await expect(
      preparedPreviewAssetLoader.model(
        { ...asset, sha256: '1'.repeat(64) },
        new AbortController().signal,
      ),
    ).rejects.toThrow('изменился');
    expect(digest).toHaveBeenCalledOnce();
  });

  it('bakes quantized node transforms into float geometry and retains the native common pivot', () => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      'position',
      new THREE.Uint16BufferAttribute(
        [65535, 0, 0, 0, 65535, 0, 0, 0, 65535],
        3,
        true,
      ),
    );
    const sourceDispose = vi.spyOn(geometry, 'dispose');
    const root = new THREE.Group();
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
    mesh.scale.set(3, 5, 7);
    root.add(mesh);
    const prototype = extractPreparedPrototype(asset, root);
    const positions = prototype.parts[0].geometry.getAttribute('position');
    expect(positions.array).toBeInstanceOf(Float32Array);
    expect(positions.getX(0)).toBe(3);
    expect(positions.getY(1)).toBe(5);
    expect(positions.getZ(2)).toBe(7);
    expect(prototype.nominalHeightM).toBe(1);
    expect(prototype.nominalRadiusM).toBe(1);
    expect(sourceDispose).toHaveBeenCalledOnce();
    disposePreparedPrototype(prototype);
  });

  it('retains foliage cutouts without unstable transparent sorting and releases its textures', () => {
    const texture = new THREE.Texture();
    const close = vi.fn();
    texture.image = { close };
    const material = new THREE.MeshStandardMaterial({
      map: texture,
      transparent: true,
      alphaTest: 0.1,
    });
    const root = new THREE.Group();
    root.add(new THREE.Mesh(new THREE.BoxGeometry(), material));
    const prototype = extractPreparedPrototype({ ...asset, lod: 'far' }, root);
    expect(material.transparent).toBe(false);
    expect(material.depthWrite).toBe(true);
    expect(material.alphaTest).toBe(0.48);
    expect(material.side).toBe(THREE.DoubleSide);
    expect(texture.colorSpace).toBe(THREE.SRGBColorSpace);
    expect(prototype.parts[0].castShadow).toBe(false);
    const dispose = vi.spyOn(texture, 'dispose');
    disposePreparedPrototype(prototype);
    expect(dispose).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
  });

  it('keeps near and far mesh foliage equally lit with filtered textures in every view', () => {
    const prototypes = (['near', 'far'] as const).map((lod) => {
      const material = new THREE.MeshStandardMaterial({
        name: 'bq_Leaf_Tilia-europaea browser',
        color: 0x88a66c,
        map: new THREE.Texture(),
        normalMap: new THREE.Texture(),
        roughness: 0.82,
        alphaTest: 0.42,
      });
      const root = new THREE.Group();
      root.add(new THREE.Mesh(new THREE.SphereGeometry(1, 8, 6), material));
      return extractPreparedPrototype({ ...asset, lod }, root);
    });
    const near = prototypes[0].parts[0];
    const far = prototypes[1].parts[0];
    const nearMaterial = near.material as THREE.MeshStandardMaterial;
    const farMaterial = far.material as THREE.MeshStandardMaterial;
    expect(farMaterial).toBeInstanceOf(THREE.MeshStandardMaterial);
    expect(farMaterial.color.equals(nearMaterial.color)).toBe(true);
    expect(farMaterial.toneMapped).toBe(nearMaterial.toneMapped);
    expect(farMaterial.roughness).toBe(nearMaterial.roughness);
    expect(farMaterial.alphaTest).toBe(nearMaterial.alphaTest);
    expect(far.billboard).toBe(false);
    expect(far.overheadOnly).toBeUndefined();
    for (const prototype of prototypes) {
      const material = prototype.parts[0].material as THREE.MeshStandardMaterial;
      for (const texture of [material.map!, material.normalMap!]) {
        expect(texture.minFilter).toBe(THREE.LinearMipmapLinearFilter);
        expect(texture.magFilter).toBe(THREE.LinearFilter);
        expect(texture.generateMipmaps).toBe(true);
        expect(texture.anisotropy).toBe(8);
      }
      expect(material.normalMap!.colorSpace).toBe(THREE.NoColorSpace);
      disposePreparedPrototype(prototype);
    }
  });

  it('keeps a complete Botaniq whole-tree bake unlit with its frozen silhouette cutoff', () => {
    const texture = new THREE.Texture();
    const source = new THREE.MeshStandardMaterial({
      name: 'whole-tree Botaniq multiview',
      map: texture,
      color: 0x245622,
      alphaTest: 0.15,
    });
    const originalDispose = vi.spyOn(source, 'dispose');
    const root = new THREE.Group();
    root.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 1), source));
    const prototype = extractPreparedPrototype({ ...asset, lod: 'far' }, root);
    const material = prototype.parts[0].material as THREE.MeshBasicMaterial;
    expect(material).toBeInstanceOf(THREE.MeshBasicMaterial);
    expect(material.map).toBe(texture);
    expect(material.color.getHex()).toBe(0xffffff);
    expect(material.toneMapped).toBe(false);
    expect(material.alphaTest).toBe(0.15);
    expect(material.transparent).toBe(false);
    expect(material.depthWrite).toBe(true);
    expect(material.side).toBe(THREE.DoubleSide);
    expect(prototype.parts[0].castShadow).toBe(false);
    expect(prototype.parts[0].billboard).toBe(false);
    expect(originalDispose).toHaveBeenCalledOnce();
    const mapDispose = vi.spyOn(texture, 'dispose');
    disposePreparedPrototype(prototype);
    expect(mapDispose).toHaveBeenCalledOnce();
  });

  it('preserves an exported unlit whole-tree material without recreating or darkening it', () => {
    const source = new THREE.MeshBasicMaterial({
      name: 'whole-tree Botaniq multiview',
      map: new THREE.Texture(),
      alphaTest: 0.35,
    });
    const root = new THREE.Group();
    root.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 1), source));
    const prototype = extractPreparedPrototype({ ...asset, lod: 'far' }, root);
    expect(prototype.parts[0].material).toBe(source);
    expect(source.toneMapped).toBe(false);
    expect(source.alphaTest).toBe(0.35);
    expect(source.map!.colorSpace).toBe(THREE.SRGBColorSpace);
    disposePreparedPrototype(prototype);
  });

  it('identifies top and street captures separately while ordinary near geometry stays unconditional', () => {
    const root = new THREE.Group();
    for (const name of [
      'whole-tree Botaniq multiview top 90',
      'whole-tree Botaniq multiview 45',
      'bq_Leaf_ordinary',
    ])
      root.add(
        new THREE.Mesh(
          new THREE.PlaneGeometry(2, 1),
          new THREE.MeshBasicMaterial({ name, alphaTest: 0.15 }),
        ),
      );
    const prototype = extractPreparedPrototype({ ...asset, lod: 'far' }, root);
    expect(prototype.parts.map((part) => part.overheadOnly)).toEqual([
      true,
      false,
      undefined,
    ]);
    disposePreparedPrototype(prototype);
  });
});
