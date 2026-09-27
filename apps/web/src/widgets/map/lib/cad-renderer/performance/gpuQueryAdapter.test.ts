import { expect, it, vi } from 'vitest';
import { createGpuQueryAdapter } from './gpuQueryAdapter';

it('adapts WebGL1 timer query operations without requiring WebGL2 methods', () => {
  const query = {};
  const ext = {
    TIME_ELAPSED_EXT: 1,
    GPU_DISJOINT_EXT: 2,
    CURRENT_QUERY_EXT: 3,
    QUERY_RESULT_EXT: 4,
    QUERY_RESULT_AVAILABLE_EXT: 5,
    createQueryEXT: vi.fn(() => query),
    beginQueryEXT: vi.fn(),
    endQueryEXT: vi.fn(),
    getQueryEXT: vi.fn(() => null),
    getQueryObjectEXT: vi.fn((_query, parameter) =>
      parameter === 5 ? true : 1_500_000,
    ),
    deleteQueryEXT: vi.fn(),
  };
  const gl = {
    getExtension: vi.fn(() => ext),
    getParameter: vi.fn(() => false),
  };
  const adapter = createGpuQueryAdapter(
    gl as unknown as WebGLRenderingContext,
  )!;
  expect(adapter.api).toBe('webgl1');
  expect(adapter.create()).toBe(query);
  adapter.begin(query);
  adapter.end();
  expect(ext.beginQueryEXT).toHaveBeenCalledWith(1, query);
  expect(ext.endQueryEXT).toHaveBeenCalledWith(1);
  expect(adapter.available(query)).toBe(true);
  expect(adapter.nanoseconds(query)).toBe(1_500_000);
  expect(adapter.isActive()).toBe(false);
  expect(adapter.isDisjoint()).toBe(false);
  adapter.remove(query);
  expect(ext.deleteQueryEXT).toHaveBeenCalledWith(query);
});
