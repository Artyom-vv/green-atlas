import type { MapFocusResult } from '@/entities/editor/model/mapFocus';
import type {
  AgentControlCommand,
  AgentControlProof,
  AgentControlResult,
  AgentRun,
  PlantingZoneAssignment,
  Project,
} from '@green/api-client';

export type AgentMapControl = (
  command: AgentControlCommand,
  geometry: PlantingZoneAssignment['geometry'],
  signal: AbortSignal,
) => Promise<MapFocusResult>;
export const CONTROL_RECEIPTS_KEY = 'green-atlas:map-control-receipts:v1';
export const CONTROL_RECEIPTS_LOCK = 'green-atlas:map-control-receipts';
type Receipt = {
  identity: string;
  result?: AgentControlResult;
  acknowledged?: boolean;
};
const receiptLimit = 128;
export const controlIdentity = (command: AgentControlCommand) =>
  JSON.stringify([
    command.project_id,
    command.run_id,
    command.execution_attempt_id,
    command.id,
    command.action,
    command.zone_id,
    command.geometry_version,
    command.geometry_digest,
  ]);
export function controlResult(
  command: AgentControlCommand,
  status: AgentControlResult['status'],
  error_code?: AgentControlResult['error_code'],
): AgentControlResult {
  return {
    command_id: command.id,
    project_id: command.project_id,
    run_id: command.run_id,
    execution_attempt_id: command.execution_attempt_id,
    zone_id: command.zone_id,
    geometry_version: command.geometry_version,
    geometry_digest: command.geometry_digest,
    status,
    ...(error_code ? { error_code } : {}),
  };
}
export function currentControlGeometry(
  command: AgentControlCommand,
  geometry: PlantingZoneAssignment['geometry'],
  project: Project | undefined,
) {
  const zones =
    project?.planting_zones?.filter((zone) => zone.id === command.zone_id) ??
    [];
  return Boolean(
    project?.id === command.project_id &&
    project.map_ready &&
    project.geometry_version === command.geometry_version &&
    zones.length === 1 &&
    zones[0].geometry.type === geometry.type &&
    JSON.stringify(zones[0].geometry.coordinates) ===
      JSON.stringify(geometry.coordinates),
  );
}
export async function verifiedControlGeometry(
  command: AgentControlCommand,
  proof: AgentControlProof,
  project: Project,
) {
  if (
    command.action !== 'focus_zone' ||
    controlIdentity(command) !== controlIdentity(proof.command) ||
    !/^[a-f0-9]{64}$/.test(command.geometry_digest)
  )
    throw new Error('CONTROL_STALE');
  const digest = Array.from(
    new Uint8Array(
      await crypto.subtle.digest(
        'SHA-256',
        new TextEncoder().encode(proof.geometry_json),
      ),
    ),
  )
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
  const geometry = JSON.parse(
    proof.geometry_json,
  ) as PlantingZoneAssignment['geometry'];
  if (
    digest !== command.geometry_digest ||
    typeof geometry.type !== 'string' ||
    !['Polygon', 'MultiPolygon'].includes(geometry.type) ||
    !currentControlGeometry(command, geometry, project)
  )
    throw new Error('CONTROL_STALE');
  const coordinates: number[][] = [];
  const points = (value: unknown): void => {
    if (!Array.isArray(value) || !value.length)
      throw new Error('CONTROL_STALE');
    if (typeof value[0] === 'number') {
      if (
        value.length < 2 ||
        !value.every(
          (item) => typeof item === 'number' && Number.isFinite(item),
        )
      )
        throw new Error('CONTROL_STALE');
      coordinates.push(value as number[]);
    } else value.forEach(points);
  };
  points(geometry.coordinates);
  const bounds = coordinates.reduce(
    (box, point) => [
      Math.min(box[0], point[0]),
      Math.min(box[1], point[1]),
      Math.max(box[2], point[0]),
      Math.max(box[3], point[1]),
    ],
    [Infinity, Infinity, -Infinity, -Infinity],
  );
  if (bounds.some((value, index) => value !== command.bounds[index]))
    throw new Error('CONTROL_STALE');
  return geometry;
}

function receipts(): Receipt[] {
  const value: unknown = JSON.parse(
    localStorage.getItem(CONTROL_RECEIPTS_KEY) ?? '[]',
  );
  if (
    !Array.isArray(value) ||
    value.length > receiptLimit ||
    value.some((item) => !item || typeof item.identity !== 'string')
  )
    throw new Error('Invalid control receipts');
  return value as Receipt[];
}
function writeReceipt(receipt: Receipt) {
  const records = receipts().filter(
    (item) => item.identity !== receipt.identity,
  );
  while (records.length >= receiptLimit) {
    const removable = records.findIndex((item) => item.acknowledged);
    if (removable < 0) throw new Error('Control receipt store full');
    records.splice(removable, 1);
  }
  records.push(receipt);
  const serialized = JSON.stringify(records);
  localStorage.setItem(CONTROL_RECEIPTS_KEY, serialized);
  if (localStorage.getItem(CONTROL_RECEIPTS_KEY) !== serialized)
    throw new Error('Control receipt not retained');
}
export function confirmedControlResult(
  command: AgentControlCommand,
  result: AgentControlResult,
  run: AgentRun,
) {
  const accepted = run.state.control_result;
  return Boolean(
    accepted &&
    run.state.project_id === command.project_id &&
    run.state.run_id === command.run_id &&
    run.state.execution_attempt_id === command.execution_attempt_id &&
    run.state.control_command &&
    controlIdentity(run.state.control_command) === controlIdentity(command) &&
    (result.status === 'completed'
      ? run.state.status === 'finished'
      : ['failed', 'cancelled'].includes(run.state.status)) &&
    [
      'command_id',
      'project_id',
      'run_id',
      'execution_attempt_id',
      'zone_id',
      'geometry_version',
      'geometry_digest',
      'status',
    ].every(
      (key) =>
        accepted[key as keyof AgentControlResult] ===
        result[key as keyof AgentControlResult],
    ) &&
    (accepted.error_code ?? null) === (result.error_code ?? null),
  );
}
export async function acknowledgeControlReceipt(command: AgentControlCommand) {
  try {
    await navigator.locks.request(CONTROL_RECEIPTS_LOCK, () => {
      const receipt = receipts().find(
        (item) => item.identity === controlIdentity(command),
      );
      if (receipt) writeReceipt({ ...receipt, acknowledged: true });
    });
  } catch {
    /* Retaining an unacknowledged receipt is safe and prevents replay. */
  }
}

/** The browser keeps outcomes, never geometry or project data. Absence of durable proof never permits replay. */
export async function performAgentControl(
  command: AgentControlCommand,
  proof: AgentControlProof,
  project: Project,
  adapter: AgentMapControl | undefined,
  signal: AbortSignal,
): Promise<AgentControlResult> {
  const unknown = () =>
    controlResult(command, 'unknown', 'CONTROL_OUTCOME_UNKNOWN');
  const perform = async (): Promise<AgentControlResult> => {
    if (signal.aborted)
      return controlResult(command, 'cancelled', 'FOCUS_INTERRUPTED');
    const identity = controlIdentity(command);
    let prior: Receipt | undefined;
    try {
      prior = receipts().find((item) => item.identity === identity);
    } catch {
      return unknown();
    }
    if (prior) {
      if (!prior.result) return unknown();
      const expected = controlResult(
        command,
        prior.result.status,
        prior.result.error_code,
      );
      return JSON.stringify(expected) === JSON.stringify(prior.result)
        ? prior.result
        : unknown();
    }
    let result: AgentControlResult;
    let geometry: PlantingZoneAssignment['geometry'];
    try {
      geometry = await verifiedControlGeometry(command, proof, project);
    } catch {
      return controlResult(command, 'failed', 'CONTROL_STALE');
    }
    if (signal.aborted)
      return controlResult(command, 'cancelled', 'FOCUS_INTERRUPTED');
    // Persist before touching the camera, including across tabs and full reloads.
    try {
      await navigator.locks.request(CONTROL_RECEIPTS_LOCK, () =>
        writeReceipt({ identity }),
      );
    } catch {
      return unknown();
    }
    if (signal.aborted)
      return controlResult(command, 'cancelled', 'FOCUS_INTERRUPTED');
    try {
      const outcome = adapter
        ? await adapter(command, geometry, signal)
        : ({ status: 'failed', error_code: 'MAP_UNAVAILABLE' } as const);
      result = controlResult(command, outcome.status, outcome.error_code);
    } catch {
      result = unknown();
    }
    try {
      await navigator.locks.request(CONTROL_RECEIPTS_LOCK, () =>
        writeReceipt({ identity, result }),
      );
    } catch {
      return unknown();
    }
    return result;
  };
  // Serializing a command across tabs avoids two consumers moving the camera together.
  if (!navigator.locks) return unknown();
  return navigator.locks.request(
    `green-atlas:map-control:${controlIdentity(command)}`,
    { signal },
    perform,
  );
}
