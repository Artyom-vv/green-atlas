import { describe, expect, it } from 'vitest';
import { workspacePersistenceState } from './workspacePersistenceState';

describe('workspace persistence proof', () => {
  it('allows leaving after a confirmed write with failed reading while blocking edits', () => {
    expect(
      workspacePersistenceState({
        hasDraft: false,
        previewPending: false,
        writes: [{ outcome: 'confirmed', readRecovery: true }],
      }),
    ).toMatchObject({
      blocksLeaving: false,
      editingBlocked: true,
      pending: false,
      writeOutcome: 'confirmed',
    });
  });
  it.each(['writing', 'unknown'] as const)(
    'keeps %s distinct from a saved receipt',
    (outcome) => {
      expect(
        workspacePersistenceState({
          hasDraft: false,
          previewPending: false,
          writes: [{ outcome, readRecovery: true }],
        }),
      ).toMatchObject({
        blocksLeaving: true,
        pending: true,
        editingBlocked: true,
        writeOutcome: outcome,
      });
    },
  );
  it('retains an unrelated draft even when another operation has a receipt', () => {
    expect(
      workspacePersistenceState({
        hasDraft: true,
        previewPending: false,
        writes: [{ outcome: 'confirmed', readRecovery: true }],
      }).blocksLeaving,
    ).toBe(true);
  });
  it('does not let one confirmed write hide an unknown competing outcome', () => {
    expect(
      workspacePersistenceState({
        hasDraft: false,
        previewPending: false,
        writes: [{ outcome: 'confirmed' }, { outcome: 'unknown' }],
      }).writeOutcome,
    ).toBe('unknown');
  });
});
