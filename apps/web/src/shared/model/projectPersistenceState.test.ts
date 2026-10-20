import { expect, it } from 'vitest';
import { projectPersistenceState } from './projectPersistenceState';

it('does not claim a write was saved when only a fresh read is needed', () => {
  const state = projectPersistenceState({
    hasDraft: false,
    previewPending: false,
    writes: [{ outcome: 'idle', readRecovery: true }],
  });
  expect(state.status).toBe('Обновите данные для продолжения');
  expect(state.blocksLeaving).toBe(false);
  expect(state.editingBlocked).toBe(true);
});
