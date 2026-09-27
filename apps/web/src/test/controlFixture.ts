import { createHash, webcrypto } from 'node:crypto';
import type { AgentControlCommand, AgentControlProof, AgentRun, Project } from '@green/api-client';
import { vi } from 'vitest';

export const controlGeometry = { type: 'Polygon', coordinates: [
  [[1.25, 2], [31.25, 2], [31.25, 42], [1.25, 42], [1.25, 2]],
  [[5, 5], [5, 10], [10, 10], [10, 5], [5, 5]],
] };
export function controlFixture() {
  const geometry_json = JSON.stringify(controlGeometry);
  const command: AgentControlCommand = { id: 'focus-1', action: 'focus_zone', project_id: 'focus-project', run_id: 'focus-run', execution_attempt_id: 'attempt-1', zone_id: 'zone-east', zone_label: 'Восточный сквер', state_version: 3, geometry_version: 2,
    geometry_digest: createHash('sha256').update(geometry_json).digest('hex'), bounds: [1.25, 2, 31.25, 42], issued_at: '2026-09-10T12:00:00Z' };
  const proof: AgentControlProof = { command, geometry_json };
  const project = { id: command.project_id, state_version: 3, geometry_version: 2, map_ready: true, plan: null,
    planting_zones: [{ id: command.zone_id, label: command.zone_label, geometry: structuredClone(controlGeometry) }] } as unknown as Project;
  const run: AgentRun = { revision: 4, created_at: '', updated_at: '', events: [], state: {
    project_id: command.project_id, run_id: command.run_id, status: 'waiting_ui', execution_attempt_id: command.execution_attempt_id,
    intent: { raw_text: 'Покажи участок Восточный сквер на карте.', goal: { operation: 'inspect' }, scope_mode: 'explicit' }, snapshot_version: 3,
    control_command: command, control_result: null, failure: null, candidate_zone_ids: [], step: 0, tool_calls: [], tool_fingerprints: [], evidence_refs: [], max_steps: 64,
  } };
  return { command, proof, project, run };
}
export function browserControlPrimitives() {
  vi.stubGlobal('crypto', webcrypto);
  const queues = new Map<string, Promise<unknown>>();
  vi.stubGlobal('navigator', { locks: { request: vi.fn((name: string, options: unknown, callback?: () => Promise<unknown>) => {
    const action = (callback ?? options) as () => Promise<unknown>;
    const next = (queues.get(name) ?? Promise.resolve()).then(action);
    queues.set(name, next.catch(() => {}));
    return next;
  }) } });
}
