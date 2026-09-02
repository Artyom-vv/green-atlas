import type { ChangeSetPreview } from '@green/api-client';

export type MoveCandidateStatus = 'allowed' | 'blocked' | 'soft_conflict' | 'unknown';
export type MoveLiveStatus = MoveCandidateStatus | 'checking';

export type MoveLiveValidation = {
  status: MoveLiveStatus;
  reason: string;
  objectStatuses: Record<string, MoveCandidateStatus>;
};

export function invalidateMovePreviewGeneration(generation: { current: number }): number {
  generation.current += 1;
  return generation.current;
}

export function isCurrentMovePreviewGeneration(generation: { current: number }, requestId: number): boolean {
  return generation.current === requestId;
}

export function moveValidationFromPreview(preview: ChangeSetPreview): MoveLiveValidation {
  const results = preview.candidate_results ?? [];
  const issue = results.find((item) => item.status === 'blocked')
    ?? results.find((item) => item.status === 'soft_conflict')
    ?? results.find((item) => item.status === 'unknown');
  const status = issue?.status ?? (preview.can_apply ? 'allowed' : 'unknown');
  return {
    status,
    reason: issue?.reason ?? (preview.can_apply ? 'Можно переместить' : 'Положение требует проверки'),
    objectStatuses: Object.fromEntries(results.flatMap((item) => item.object_id ? [[item.object_id, item.status]] : [])),
  };
}
