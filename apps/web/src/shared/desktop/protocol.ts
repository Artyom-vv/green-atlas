export const DESKTOP_PROTOCOL_VERSION = 1;
import type { WriteOutcome } from '@/shared/model/writeOutcome';
export type { WriteOutcome } from '@/shared/model/writeOutcome';

export type DesktopAction = 'library' | 'handoff' | 'close' | 'quit';

export interface DesktopCapabilities {
  schemaVersion: 1;
  type: 'capabilities';
  origin: string;
  documentGeneration: number;
}

export interface DesktopRuntimeEvent {
  schemaVersion: 1;
  type: 'runtimeUnavailable' | 'runtimeRestored';
  origin: string;
  documentGeneration: number;
}

export interface DesktopNavigationRequest {
  schemaVersion: 1;
  type: 'navigationRequest';
  origin: string;
  documentGeneration: number;
  requestId: number;
  action: DesktopAction;
  target?: string;
}

export interface DesktopWorkState {
  hasDraft: boolean;
  writeOutcome: WriteOutcome;
  readRecovery: boolean;
  editingBlocked: boolean;
}

const positiveInteger = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value > 0;

export function isDesktopProjectTarget(value: unknown): value is string {
  if (typeof value !== 'string' || value !== value.trim()) return false;
  if (value === '/projects') return true;
  return /^\/projects\/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\/(?:setup(?:\?open=editor)?|workspace|import\?source=cad)$/.test(
    value,
  );
}

/** Host payloads are data. Accept only this document's protocol and origin. */
export function parseDesktopMessage(
  input: unknown,
  origin: string,
):
  | DesktopCapabilities
  | DesktopNavigationRequest
  | DesktopRuntimeEvent
  | undefined {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return;
  const value = input as Record<string, unknown>;
  if (
    value.schemaVersion !== DESKTOP_PROTOCOL_VERSION ||
    value.origin !== origin ||
    !positiveInteger(value.documentGeneration)
  )
    return;
  if (value.type === 'capabilities')
    return value as unknown as DesktopCapabilities;
  if (value.type === 'runtimeUnavailable' || value.type === 'runtimeRestored')
    return value as unknown as DesktopRuntimeEvent;
  if (value.type !== 'navigationRequest' || !positiveInteger(value.requestId))
    return;
  if (
    typeof value.action !== 'string' ||
    !['library', 'handoff', 'close', 'quit'].includes(value.action)
  )
    return;
  if (value.action === 'library' && value.target !== '/projects') return;
  if (value.action === 'handoff' && !isDesktopProjectTarget(value.target))
    return;
  if (
    (value.action === 'close' || value.action === 'quit') &&
    value.target !== undefined
  )
    return;
  return value as unknown as DesktopNavigationRequest;
}
