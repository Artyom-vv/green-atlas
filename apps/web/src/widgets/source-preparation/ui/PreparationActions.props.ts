import type { SourcePreparationState } from '../model/sourcePreparation';
export interface PreparationActionsProps extends Pick<
  SourcePreparationState,
  | 'preparationBlocked'
  | 'reviewOnly'
  | 'calculating'
  | 'statusUnknown'
  | 'readinessBlockedReason'
  | 'checkingStatus'
  | 'saveMutation'
  | 'mappingsChanged'
> {
  onImport: () => void;
  onPlan: () => void;
  mapReady: boolean;
  cadPreview?: boolean;
}
export const PreparationActionsPropsFor = (
  state: Pick<
    SourcePreparationState,
    | 'preparationBlocked'
    | 'reviewOnly'
    | 'calculating'
    | 'statusUnknown'
    | 'readinessBlockedReason'
    | 'checkingStatus'
    | 'saveMutation'
    | 'mappingsChanged'
  > & { cadPreview?: boolean },
) => ({
  preparationBlocked: state.preparationBlocked,
  reviewOnly: state.reviewOnly,
  cadPreview: state.cadPreview,
  calculating: state.calculating,
  statusUnknown: state.statusUnknown,
  readinessBlockedReason: state.readinessBlockedReason,
  checkingStatus: state.checkingStatus,
  saveMutation: state.saveMutation,
  mappingsChanged: state.mappingsChanged,
});
