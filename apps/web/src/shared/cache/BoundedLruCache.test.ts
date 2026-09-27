import { BoundedLruCache } from '@/shared/cache/BoundedLruCache';
import { describe, expect, it } from 'vitest';

describe('BoundedLruCache', () => {
  it('returns a deleted value and releases its capacity without reordering survivors', () => {
    const cache = new BoundedLruCache<string, number>(2);
    cache.set('first', 0);
    cache.set('second', 2);
    expect(cache.delete('first')).toBe(0);
    expect(cache.delete('missing')).toBeUndefined();
    expect(cache.has('first')).toBe(false);
    expect(cache.size).toBe(1);
    expect(cache.set('third', 3)).toEqual([]);
    expect([...cache.values()]).toEqual([2, 3]);
  });
  it('evicts the least recently used value and keeps touched viewport entries', () => {
    const cache = new BoundedLruCache<string, number>(3);
    cache.set('old-a', 1);
    cache.set('active', 2);
    cache.set('old-b', 3);
    expect(cache.touch('active')).toBe(true);

    expect(cache.set('incoming', 4)).toEqual([1]);
    expect([...cache.values()]).toEqual([3, 2, 4]);
    expect(cache.size).toBe(3);
  });

  it('rejects an invalid capacity', () => {
    expect(() => new BoundedLruCache(0)).toThrow(/positive integer/);
  });
});
