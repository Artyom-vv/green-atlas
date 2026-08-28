export class BoundedLruCache<Key, Value> {
  private readonly entries = new Map<Key, Value>();

  constructor(readonly capacity: number) {
    if (!Number.isInteger(capacity) || capacity < 1) throw new Error('Cache capacity must be a positive integer');
  }

  get size() {
    return this.entries.size;
  }

  has(key: Key) {
    return this.entries.has(key);
  }

  get(key: Key) {
    const value = this.entries.get(key);
    if (value === undefined) return undefined;
    this.entries.delete(key);
    this.entries.set(key, value);
    return value;
  }

  values() {
    return this.entries.values();
  }

  clear() {
    this.entries.clear();
  }

  touch(key: Key) {
    return this.get(key) !== undefined;
  }

  set(key: Key, value: Value) {
    this.entries.delete(key);
    this.entries.set(key, value);
    const evicted: Value[] = [];
    while (this.entries.size > this.capacity) {
      const oldest = this.entries.keys().next();
      if (oldest.done) break;
      const removed = this.entries.get(oldest.value);
      this.entries.delete(oldest.value);
      if (removed !== undefined) evicted.push(removed);
    }
    return evicted;
  }
}
