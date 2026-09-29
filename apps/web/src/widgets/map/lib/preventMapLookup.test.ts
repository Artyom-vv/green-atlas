import { describe, expect, it } from 'vitest';
import { preventMapLookup } from './preventMapLookup';

describe('map Force Touch guard', () => {
  it('prevents dictionary lookup on the map and releases the listener on unmount', () => {
    const target = document.createElement('div');
    const detach = preventMapLookup(target);
    const first = new Event('webkitmouseforcewillbegin', { cancelable: true });
    target.dispatchEvent(first);
    expect(first.defaultPrevented).toBe(true);

    detach();
    const second = new Event('webkitmouseforcewillbegin', { cancelable: true });
    target.dispatchEvent(second);
    expect(second.defaultPrevented).toBe(false);
  });
});
