import type { AgentRun } from '@green/api-client';

type Data = Record<string, unknown>;
const record = (value: unknown): Data | undefined =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Data)
    : undefined;
const version = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0;
const area = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0;

/** Historical effects require the saved verified preview and its exact applied receipt. */
export function committedZoneChange(run: AgentRun | undefined) {
  if (run?.state.status !== 'finished') return undefined;
  const boundary = [...run.events]
    .reverse()
    .find((event) =>
      ['run_restarted', 'question_answered'].includes(event.kind),
    );
  const events = run.events.filter(
    (event) => !boundary || event.sequence > boundary.sequence,
  );
  const commits = events.filter(
    (event) =>
      event.kind === 'commit_applied' &&
      event.payload.kind === 'planting_zones',
  );
  if (commits.length !== 1) return undefined;
  const commit = commits[0];
  const receipt = commit.payload;
  if (
    receipt.status !== 'applied' ||
    receipt.plantings_unchanged !== true ||
    receipt.project_id !== run.state.project_id ||
    typeof receipt.preview_id !== 'string' ||
    !receipt.preview_id ||
    typeof receipt.preview_ref !== 'string' ||
    typeof receipt.digest !== 'string' ||
    !/^[a-f0-9]{64}$/.test(receipt.digest) ||
    !version(receipt.base_state_version) ||
    receipt.state_version !== receipt.base_state_version + 1 ||
    receipt.state_version !== run.state.snapshot_version ||
    receipt.plan_version !== run.state.plan_version ||
    run.state.outcome_ref !== `zone-change:${receipt.preview_id}`
  )
    return undefined;
  const results = events.filter(
    (event) =>
      event.kind === 'tool_result' &&
      event.sequence < commit.sequence &&
      event.payload.call_id === receipt.preview_ref,
  );
  if (results.length !== 1) return undefined;
  const result = results[0].payload;
  const data = record(result.data);
  if (
    result.name !== 'prepare_zone_change' ||
    result.status !== 'succeeded' ||
    record(result.verification)?.status !== 'verified' ||
    !Array.isArray(result.evidence_refs) ||
    !result.evidence_refs.includes(
      `zone-preview:${run.state.project_id}:${run.state.run_id}:${receipt.preview_ref}:${receipt.preview_id}`,
    ) ||
    !data ||
    data.id !== receipt.preview_id ||
    data.digest !== receipt.digest ||
    data.project_id !== receipt.project_id ||
    data.operation !== receipt.operation ||
    data.target_zone_id !== receipt.target_zone_id ||
    data.base_state_version !== receipt.base_state_version ||
    !version(data.base_geometry_version) ||
    receipt.geometry_version !== data.base_geometry_version + 1 ||
    data.base_plan_version !== receipt.plan_version ||
    data.can_apply !== true ||
    !Array.isArray(data.blockers) ||
    data.blockers.length ||
    data.affected_planting_count !== 0 ||
    !Array.isArray(data.affected_planting_ids) ||
    data.affected_planting_ids.length
  )
    return undefined;
  const before = record(data.target_before);
  const after = record(data.target_after);
  const target = (item: Data | undefined) =>
    item?.id === receipt.target_zone_id &&
    typeof item?.label === 'string' &&
    Boolean(item.label.trim());
  if (
    (data.operation === 'create' &&
      (data.target_before !== null ||
        !target(after) ||
        data.before_area_m2 !== null ||
        !area(data.after_area_m2))) ||
    (data.operation === 'delete' &&
      (!target(before) ||
        data.target_after !== null ||
        !area(data.before_area_m2) ||
        data.after_area_m2 !== null)) ||
    (data.operation === 'update' &&
      (!target(before) ||
        !target(after) ||
        !area(data.before_area_m2) ||
        !area(data.after_area_m2))) ||
    !['create', 'update', 'delete'].includes(String(data.operation)) ||
    typeof data.geometry_changed !== 'boolean'
  )
    return undefined;
  const renamed = before?.label !== after?.label;
  if (data.operation === 'update' && !renamed && !data.geometry_changed)
    return undefined;
  const title =
    data.operation === 'create'
      ? 'Участок создан'
      : data.operation === 'delete'
        ? 'Участок удалён'
        : data.geometry_changed
          ? renamed
            ? 'Участок изменён'
            : 'Контур участка изменён'
          : 'Участок переименован';
  return {
    title,
    beforeLabel: before?.label as string | undefined,
    afterLabel: after?.label as string | undefined,
    beforeArea: data.before_area_m2 as number | null,
    afterArea: data.after_area_m2 as number | null,
  };
}
