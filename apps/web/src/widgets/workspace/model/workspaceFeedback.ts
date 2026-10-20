interface FeedbackInput {
  placementPending: boolean;
  placementPhase?: 'checking';
  localToolVisible: boolean;
  localWriteVisible: boolean;
  savingPlan: boolean;
  releasePending: boolean;
  releaseVisible: boolean;
}

/** A projection of command state and visible owners, never another busy store. */
export function workspaceFeedback(input: FeedbackInput) {
  const localPlacement = input.placementPending && input.localToolVisible;
  const localWrite =
    input.savingPlan && (input.localWriteVisible || input.localToolVisible);
  return {
    quietPersistence: localPlacement || localWrite,
    headerWrite: input.savingPlan && !localWrite && !localPlacement,
    headerPlacement: input.placementPending && !input.localToolVisible,
    placementLabel: 'Проверяем размещение',
    headerRelease: input.releasePending && !input.releaseVisible,
  };
}
