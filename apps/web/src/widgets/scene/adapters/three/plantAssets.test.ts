import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import {
  acquirePlantAssetLibrary,
  type PlantAssetLibraryLease,
} from '@/widgets/scene/adapters/three/plantAssets';

const loaders = vi.hoisted(() => ({ load: vi.fn() }));
vi.mock('three/examples/jsm/loaders/GLTFLoader.js', () => ({
  GLTFLoader: class {
    setMeshoptDecoder() {
      return this;
    }
    loadAsync = loaders.load;
  },
}));

const manifest = {
  version: 1,
  archetypes: {
    'broadleaf-round': { lods: [{ lod: 'far', url: '/tree-far.glb' }] },
  },
};
const response = () => ({ ok: true, json: async () => manifest });
const fetchManifest = vi.fn();
const leases: PlantAssetLibraryLease[] = [];
const acquire = () => {
  const lease = acquirePlantAssetLibrary();
  leases.push(lease);
  return lease;
};

beforeEach(() => {
  vi.useFakeTimers();
  fetchManifest.mockReset().mockImplementation(async () => response());
  vi.stubGlobal('fetch', fetchManifest);
  loaders.load.mockReset().mockImplementation(async () => {
    const scene = new THREE.Group();
    scene.add(
      new THREE.Mesh(
        new THREE.BoxGeometry(2, 5, 2),
        new THREE.MeshBasicMaterial(),
      ),
    );
    return { scene };
  });
});

afterEach(async () => {
  leases.splice(0).forEach((lease) => lease.release());
  await vi.advanceTimersByTimeAsync(30_000);
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('plant asset cache recovery', () => {
  it('fetches a fresh manifest immediately after a failed network attempt', async () => {
    fetchManifest.mockRejectedValueOnce(new Error('temporary network failure'));
    const failedLease = acquire();
    const failed = await failedLease.library;
    expect(failed.loadedModelCount()).toBe(0);
    const disposed = vi.spyOn(failed, 'dispose');
    failedLease.release();
    expect(disposed).toHaveBeenCalledOnce();
    const retry = acquire();
    expect((await retry.library).loadedModelCount()).toBe(1);
    expect(fetchManifest).toHaveBeenCalledTimes(2);
    expect(loaders.load).toHaveBeenCalledExactlyOnceWith('/tree-far.glb');
  });

  it('retries failed model downloads instead of reusing an empty decoded library', async () => {
    loaders.load.mockRejectedValueOnce(new Error('temporary GLB failure'));
    const failedLease = acquire();
    expect((await failedLease.library).loadedModelCount()).toBe(0);
    failedLease.release();
    expect((await acquire().library).loadedModelCount()).toBe(1);
    expect(fetchManifest).toHaveBeenCalledTimes(2);
    expect(loaders.load).toHaveBeenCalledTimes(2);
  });

  it('retains a healthy library across overlapping leases and quick 2D/3D switches', async () => {
    const first = acquire(),
      second = acquire();
    expect(first.library).toBe(second.library);
    const assets = await first.library;
    const disposed = vi.spyOn(assets, 'dispose');
    first.release();
    first.release();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(disposed).not.toHaveBeenCalled();
    second.release();
    await vi.advanceTimersByTimeAsync(15_000);
    const reopened = acquire();
    expect(reopened.library).toBe(second.library);
    expect(fetchManifest).toHaveBeenCalledOnce();
    reopened.release();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(disposed).toHaveBeenCalledOnce();
  });

  it('does not let the last failed lease dispose or evict a recovered library', async () => {
    fetchManifest.mockResolvedValueOnce({ ok: false, status: 503 });
    const first = acquire(),
      overlapping = acquire();
    const failed = await first.library;
    const failedDisposed = vi.spyOn(failed, 'dispose');
    first.release();
    expect(failedDisposed).not.toHaveBeenCalled();
    const retry = acquire();
    const recovered = await retry.library;
    const recoveredDisposed = vi.spyOn(recovered, 'dispose');
    overlapping.release();
    expect(failedDisposed).toHaveBeenCalledOnce();
    expect(recoveredDisposed).not.toHaveBeenCalled();
    expect(acquire().library).toBe(retry.library);
    expect(fetchManifest).toHaveBeenCalledTimes(2);
  });

  it('disposes a late abandoned result without replacing the current cache entry', async () => {
    let finishAbandoned!: (value: ReturnType<typeof response>) => void;
    fetchManifest.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishAbandoned = resolve;
        }),
    );
    const abandoned = acquire();
    abandoned.release();
    await vi.advanceTimersByTimeAsync(30_000);
    const current = acquire();
    const currentAssets = await current.library;
    const disposed = vi.spyOn(currentAssets, 'dispose');
    finishAbandoned(response());
    const abandonedAssets = await abandoned.library;
    expect(abandonedAssets.loadedModelCount()).toBe(0);
    expect(disposed).not.toHaveBeenCalled();
    expect(acquire().library).toBe(current.library);
    expect(fetchManifest).toHaveBeenCalledTimes(2);
  });
});
