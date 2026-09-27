import type { SourcePreparationState } from '../model/sourcePreparation';
export interface PreparationStatusProps extends Pick<
  SourcePreparationState,
  | 'preparationRecovery'
  | 'statusUnknown'
  | 'operation'
  | 'statusQuery'
  | 'checkingStatus'
  | 'previousSourceOperation'
  | 'cancelOperation'
  | 'saveMutation'
  | 'readinessBlockedReason'
  | 'downloadSource'
> {}
export const PreparationStatusPropsFor = (
  state: Pick<
    SourcePreparationState,
    | 'preparationRecovery'
    | 'statusUnknown'
    | 'operation'
    | 'statusQuery'
    | 'checkingStatus'
    | 'previousSourceOperation'
    | 'cancelOperation'
    | 'saveMutation'
    | 'readinessBlockedReason'
    | 'downloadSource'
  >,
) => ({
  preparationRecovery: state.preparationRecovery,
  statusUnknown: state.statusUnknown,
  operation: state.operation,
  statusQuery: state.statusQuery,
  checkingStatus: state.checkingStatus,
  previousSourceOperation: state.previousSourceOperation,
  cancelOperation: state.cancelOperation,
  saveMutation: state.saveMutation,
  readinessBlockedReason: state.readinessBlockedReason,
  downloadSource: state.downloadSource,
});
