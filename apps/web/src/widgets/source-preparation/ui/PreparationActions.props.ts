import type { SourcePreparationState } from '../model/sourcePreparation';
export interface PreparationActionsProps extends Pick<
  SourcePreparationState,
  | 'preparationBlocked'
  | 'reviewOnly'
  | 'calculating'
  | 'statusUnknown'
  | 'readinessBlockedReason'
  | 'readinessSection'
  | 'partialGeometryPending'
  | 'checkingStatus'
  | 'saveMutation'
  | 'mappingsChanged'
  | 'openEditor'
  | 'calculationPending'
> {
  onImport: () => void;
  onPlan: () => void;
  mapReady: boolean;
  cadPreview?: boolean;
  onNeedsReview?: (section: string) => void;
}
export const PreparationActionsPropsFor = (
  state: Pick<
    SourcePreparationState,
    | 'preparationBlocked'
    | 'reviewOnly'
    | 'calculating'
    | 'statusUnknown'
    | 'readinessBlockedReason'
    | 'readinessSection'
    | 'partialGeometryPending'
    | 'checkingStatus'
    | 'saveMutation'
    | 'mappingsChanged'
    | 'openEditor'
    | 'calculationPending'
  > & { cadPreview?: boolean },
) => ({
  preparationBlocked: state.preparationBlocked,
  reviewOnly: state.reviewOnly,
  cadPreview: state.cadPreview,
  calculating: state.calculating,
  statusUnknown: state.statusUnknown,
  readinessBlockedReason: state.readinessBlockedReason,
  readinessSection: state.readinessSection,
  partialGeometryPending: state.partialGeometryPending,
  checkingStatus: state.checkingStatus,
  saveMutation: state.saveMutation,
  mappingsChanged: state.mappingsChanged,
  openEditor: state.openEditor,
  calculationPending: state.calculationPending,
});
