import { describe, expect, it } from 'vitest';
import { BoundedLruCache } from './BoundedLruCache';

describe('BoundedLruCache', () => {
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
