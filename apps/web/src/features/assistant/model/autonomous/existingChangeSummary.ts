import { countLabel } from '@/shared/format/countLabel';
import type { AgentRun } from '@green/api-client';

type Data = Record<string, unknown>;
export type ExistingAction = 'delete' | 'lock' | 'unlock' | 'move' | 'species';
export type ExistingChangeSummary = {
  action: ExistingAction;
  title: string;
  count: number;
  zoneIds: string[];
  unassignedZones: number;
  speciesIds: string[];
  previousSpeciesIds: string[];
  kinds: { tree: number; shrub: number };
  move?: { dx: number; dy: number };
};
const record = (value: unknown): Data | undefined =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Data)
    : undefined;
const rows = (value: unknown) =>
  Array.isArray(value)
    ? value.map(record).filter((item): item is Data => Boolean(item))
    : [];
const strings = (value: unknown) =>
  Array.isArray(value) && value.every((item) => typeof item === 'string')
    ? (value as string[])
    : [];
const sameIds = (first: string[], second: string[]) => {
  const secondIds = new Set(second);
  return (
    first.length === second.length &&
    new Set(first).size === first.length &&
    secondIds.size === second.length &&
    first.every((id) => secondIds.has(id))
  );
};
const ids = (items: Data[]) =>
  items.flatMap((item) => (typeof item.id === 'string' ? [item.id] : []));
const unique = (items: Data[], key: string) => [
  ...new Set(
    items.flatMap((item) =>
      typeof item[key] === 'string' ? [item[key] as string] : [],
    ),
  ),
];
const finite = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

export function existingChangeResult(run: AgentRun | undefined) {
  const events = run?.events ?? [];
  const boundary = [...events]
    .reverse()
    .find((event) =>
      ['run_restarted', 'question_answered'].includes(event.kind),
    );
  const current = events
    .filter(
      (event) =>
        event.kind === 'tool_result' &&
        (!boundary || event.sequence > boundary.sequence),
    )
    .map((event) => event.payload);
  const last = run?.state.last_result;
  if (
    last &&
    (!boundary || current.some((result) => result.call_id === last.call_id))
  )
    current.push(last);
  const existing = current.filter(
    (result) => result.name === 'prepare_existing_change',
  );
  const previewRef =
    run?.state.status === 'waiting_approval'
      ? run.state.pending_approval?.preview_ref
      : undefined;
  return (
    [...existing]
      .reverse()
      .find((result) => previewRef && result.call_id === previewRef) ??
    existing.at(-1)
  );
}

/** Describe only an exact, verified change to the same saved targets shown for approval. */
export function existingChangeSummary(
  run: AgentRun | undefined,
): ExistingChangeSummary | undefined {
  const result = existingChangeResult(run);
  if (
    result?.status !== 'succeeded' ||
    record(result.verification)?.status !== 'verified'
  )
    return undefined;
  if (
    run?.state.status === 'waiting_approval' &&
    result.call_id !== run.state.pending_approval?.preview_ref
  )
    return undefined;
  const data = record(result.data);
  const change = record(data?.change_set);
  if (!data || change?.can_apply !== true || change.additions_count !== 0)
    return undefined;
  const action =
    data.operation === 'delete'
      ? 'delete'
      : data.operation === 'edit'
        ? data.edit_action
        : undefined;
  if (!['delete', 'lock', 'unlock', 'move', 'species'].includes(String(action)))
    return undefined;
  const targetIds = strings(data.target_ids);
  const before = rows(data.target_before);
  const updates = rows(data.verified_updates);
  const affected = strings(
    action === 'delete' ? change.deletion_ids : change.update_ids,
  );
  const amount = affected.length;
  if (
    !amount ||
    !sameIds(affected, targetIds) ||
    !sameIds(affected, ids(before)) ||
    before.length !== amount ||
    data.requested !== amount ||
    data.found !== amount ||
    data.shortfall !== 0
  )
    return undefined;
  if (
    action === 'delete'
      ? change.deletions_count !== amount ||
        change.updates_count !== 0 ||
        updates.length > 0
      : change.updates_count !== amount ||
        change.deletions_count !== 0 ||
        !sameIds(affected, ids(updates)) ||
        updates.length !== amount
  )
    return undefined;
  let move: ExistingChangeSummary['move'];
  if (action !== 'delete') {
    const beforeById = new Map(before.map((item) => [item.id, item]));
    const editable = [
      'kind',
      'x',
      'y',
      'species_revision_id',
      'planting_zone_id',
      'locked',
      'radius',
      'layout_radius_m',
      'size_class',
      'spacing_policy',
      'pattern_id',
      'group_ids',
    ];
    const allowed =
      action === 'move'
        ? ['x', 'y']
        : action === 'species'
          ? ['species_revision_id']
          : ['locked'];
    for (const after of updates) {
      const original = beforeById.get(after.id)!;
      // A label such as “lock” must not conceal a species or position change.
      if (
        editable.some(
          (key) =>
            !allowed.includes(key) &&
            JSON.stringify(original[key] ?? null) !==
              JSON.stringify(after[key] ?? null),
        )
      )
        return undefined;
      if (
        (action === 'lock' && after.locked !== true) ||
        (action === 'unlock' && after.locked !== false)
      )
        return undefined;
      if (
        action === 'species' &&
        (typeof after.species_revision_id !== 'string' ||
          !after.species_revision_id)
      )
        return undefined;
      if (action === 'move') {
        if (
          !finite(original.x) ||
          !finite(original.y) ||
          !finite(after.x) ||
          !finite(after.y)
        )
          return undefined;
        const delta = { dx: after.x - original.x, dy: after.y - original.y };
        if (
          move &&
          (Math.abs(move.dx - delta.dx) > 1e-7 ||
            Math.abs(move.dy - delta.dy) > 1e-7)
        )
          return undefined;
        move = delta;
      }
    }
  }
  const genitive = countLabel(amount, 'посадки', 'посадок', 'посадок');
  const accusative = countLabel(amount, 'посадку', 'посадки', 'посадок');
  const title =
    action === 'delete'
      ? `Удалить ${accusative}`
      : action === 'lock'
        ? `Закрепить ${accusative}`
        : action === 'unlock'
          ? `Снять закрепление с ${genitive}`
          : action === 'move'
            ? `Переместить ${accusative}`
            : `Изменить породу у ${genitive}`;
  return {
    action: action as ExistingAction,
    title,
    count: amount,
    move,
    zoneIds: unique(before, 'planting_zone_id'),
    unassignedZones: before.filter((item) => !item.planting_zone_id).length,
    speciesIds: unique(
      action === 'species' ? updates : before,
      'species_revision_id',
    ),
    previousSpeciesIds: unique(before, 'species_revision_id'),
    kinds: {
      tree: before.filter((item) => item.kind === 'tree').length,
      shrub: before.filter((item) => item.kind === 'shrub').length,
    },
  };
}
