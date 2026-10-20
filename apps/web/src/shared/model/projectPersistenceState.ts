import type { WriteOutcome } from '@/shared/model/writeOutcome';

export interface ProjectWriteState {
  outcome: WriteOutcome;
  readRecovery?: boolean;
}

interface PersistenceInput {
  hasDraft: boolean;
  previewPending: boolean;
  writes: readonly ProjectWriteState[];
}

/** Persistence proof governs leaving; a current snapshot governs editing. */
export function projectPersistenceState({
  hasDraft,
  previewPending,
  writes,
}: PersistenceInput) {
  const outcome: WriteOutcome = writes.some(
    (write) => write.outcome === 'writing',
  )
    ? 'writing'
    : writes.some((write) => write.outcome === 'unknown')
      ? 'unknown'
      : writes.some((write) => write.outcome === 'confirmed')
        ? 'confirmed'
        : 'idle';
  const readRecovery = writes.some((write) => write.readRecovery);
  const pending =
    outcome === 'writing' || outcome === 'unknown' || previewPending;
  return {
    hasDraft,
    writeOutcome: outcome,
    readRecovery,
    pending,
    blocksLeaving: hasDraft || pending,
    editingBlocked: pending || readRecovery,
    status:
      outcome === 'writing'
        ? 'Сохраняем…'
        : outcome === 'unknown'
          ? 'Проверяем результат сохранения'
          : readRecovery
            ? outcome === 'confirmed'
              ? 'Сохранено. Обновите данные для продолжения'
              : 'Обновите данные для продолжения'
            : hasDraft
              ? 'Есть неприменённые изменения'
              : 'Изменения сохранены',
  };
}
