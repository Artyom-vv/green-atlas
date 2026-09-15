import { frameStatistics } from './frameStatistics';
import {
  latencyStatistics,
  normalizeInputTimestamp,
} from './latencyStatistics';

export class GestureSamples {
  private frames: number[] = [];
  private pending: number[] = [];
  private latencies: number[] = [];
  private dispatchDelays: number[] = [];
  private events: Record<string, number> = {};
  private activeFrames = 0;
  private timestampFallbacks = 0;
  private trustedEvents = 0;

  input(event: Event, dispatchedAt: number, timeOrigin: number) {
    this.events[event.type] = (this.events[event.type] ?? 0) + 1;
    if (event.isTrusted) this.trustedEvents += 1;
    const motion =
      event.type === 'wheel' ||
      (event.type === 'pointermove' && (event as PointerEvent).buttons !== 0);
    if (!motion) return;
    const timestamp = normalizeInputTimestamp(
      event.timeStamp,
      dispatchedAt,
      timeOrigin,
    );
    if (timestamp.fallback) this.timestampFallbacks += 1;
    this.dispatchDelays.push(dispatchedAt - timestamp.time);
    this.pending.push(timestamp.time);
  }

  frame(time: number) {
    this.frames.push(time);
    if (this.pending.length) this.activeFrames += 1;
    for (const inputTime of this.pending) this.latencies.push(time - inputTime);
    this.pending = [];
  }

  result(endedAt: number) {
    const frames = frameStatistics(this.frames);
    return {
      events: this.events,
      trustedEvents: this.trustedEvents,
      timestampFallbacks: this.timestampFallbacks,
      renderedFrames: this.frames.length,
      activeFrames: this.activeFrames,
      activeFrameDefinition:
        'postrender with pending wheel or pressed pointermove',
      inputToNextPostrender: latencyStatistics(this.latencies),
      inputDispatchDelay: latencyStatistics(this.dispatchDelays),
      pendingMotionEvents: this.pending.length,
      oldestPendingAgeMs: this.pending.length
        ? endedAt - this.pending[0]
        : null,
      intervalsIncludingIdle: {
        durationMs: frames.durationMs,
        p95Ms: frames.p95Ms,
        maxMs: frames.maxMs,
        over50Ms: frames.over50Ms,
      },
      gpuCompletionMeasured: false,
    };
  }
}
