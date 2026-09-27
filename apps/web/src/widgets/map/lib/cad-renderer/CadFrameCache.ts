import type { CadFrame } from './camera';

/** A static CAD canvas remains reusable while only OL overlays change. */
export class CadFrameCache {
  private last: number[] | undefined;

  render(frame: CadFrame, draw: () => void): void {
    const { center, resolution, rotation } = frame.viewState;
    const next = [
      frame.size[0],
      frame.size[1],
      center[0],
      center[1],
      resolution,
      rotation,
    ];
    if (frame.size[0] <= 0 || frame.size[1] <= 0) {
      this.invalidate();
      return;
    }
    if (this.last?.every((value, index) => value === next[index])) return;
    draw();
    // Never reuse a frame whose render failed, or a mutable OL view array.
    this.last = next;
  }

  invalidate(): void {
    this.last = undefined;
  }
}
