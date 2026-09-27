import type { AgentRun, ZoneChangePreview } from '@green/api-client';

export function zonePreviewFixture(operation: 'create' | 'rename' | 'contour' | 'delete' = 'rename'): ZoneChangePreview {
  const geometry = { type: 'Polygon', coordinates: [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]] };
  const contour = { type: 'Polygon', coordinates: [[[0, 0], [12, 0], [12, 10], [0, 10], [0, 0]]] };
  const before = { id: 'target', label: 'Северный сквер', geometry };
  const after = { ...before, label: operation === 'contour' ? before.label : 'Липовый сквер', geometry: operation === 'contour' ? contour : geometry };
  const untouched = { id: 'untouched', label: 'Другой участок', geometry: contour };
  const action = operation === 'rename' || operation === 'contour' ? 'update' : operation;
  return { id: 'zone-preview', project_id: 'project-1', operation: action, target_zone_id: 'target',
    draft: { operation: action, base_state_version: 3, ...(action !== 'create' ? { zone_id: 'target' } : {}),
      ...(operation === 'create' || operation === 'rename' ? { label: after.label } : {}),
      ...(operation === 'create' || operation === 'contour' ? { geometry: after.geometry } : {}) },
    base_state_version: 3, base_geometry_version: 1, base_plan_version: 2,
    before_zones: operation === 'create' ? [untouched] : [before, untouched],
    after_zones: operation === 'delete' ? [untouched] : [after, untouched],
    before_area_m2: operation === 'create' ? null : 100,
    after_area_m2: operation === 'delete' ? null : operation === 'contour' ? 120 : 100,
    planting_digest: 'a'.repeat(64), can_apply: true, blockers: [], affected_planting_ids: [], preflight: null,
    created_at: '2026-09-10T10:00:00Z', expires_at: '2099-01-01T00:00:00Z', digest: 'b'.repeat(64) };
}

export function zoneRunFixture(): AgentRun {
  return { revision: 3, created_at: '', updated_at: '', events: [], state: {
    run_id: 'zone-run', project_id: 'project-1', status: 'waiting_approval', intent: { raw_text: 'Переименуй Северный сквер в «Липовый сквер»', goal: { operation: 'edit' }, scope_mode: 'explicit' },
    pending_approval: { kind: 'planting_zones', preview_ref: 'zone-call' }, snapshot_version: 3, plan_version: 2,
    candidate_zone_ids: [], step: 1, tool_calls: [], tool_fingerprints: [], evidence_refs: [], max_steps: 64,
  } };
}

export function committedZoneRunFixture(operation: Parameters<typeof zonePreviewFixture>[0] = 'rename'): AgentRun {
  const preview = zonePreviewFixture(operation);
  const run = zoneRunFixture();
  const before = preview.before_zones.find(zone => zone.id === preview.target_zone_id);
  const after = preview.after_zones.find(zone => zone.id === preview.target_zone_id);
  run.state.status = 'finished'; run.state.pending_approval = null;
  run.state.snapshot_version = 4; run.state.outcome_ref = `zone-change:${preview.id}`;
  run.events = [
    { sequence: 1, kind: 'tool_result', created_at: '', payload: {
      name: 'prepare_zone_change', call_id: 'zone-call', status: 'succeeded', verification: { status: 'verified' },
      evidence_refs: [`zone-preview:${preview.project_id}:${run.state.run_id}:zone-call:${preview.id}`],
      data: { id: preview.id, digest: preview.digest, project_id: preview.project_id, operation: preview.operation,
        target_zone_id: preview.target_zone_id, base_state_version: preview.base_state_version,
        base_geometry_version: preview.base_geometry_version, base_plan_version: preview.base_plan_version,
        can_apply: true, blockers: [], affected_planting_count: 0, affected_planting_ids: [],
        before_area_m2: preview.before_area_m2, after_area_m2: preview.after_area_m2,
        target_before: before ? { id: before.id, label: before.label } : null,
        target_after: after ? { id: after.id, label: after.label } : null, geometry_changed: operation === 'contour' },
    } },
    { sequence: 2, kind: 'commit_applied', created_at: '', payload: {
      kind: 'planting_zones', status: 'applied', preview_ref: 'zone-call', preview_id: preview.id, digest: preview.digest,
      project_id: preview.project_id, operation: preview.operation, target_zone_id: preview.target_zone_id,
      base_state_version: preview.base_state_version, state_version: 4, geometry_version: 2, plan_version: 2, plantings_unchanged: true,
    } },
  ];
  return run;
}
