import type { SourcePreparationState } from '../model/sourcePreparation';
import type { ReactNode } from 'react';
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
  | 'capturePreparationNeeded'
  | 'prepareCapture'
  | 'saveMutation'
  | 'mappingsChanged'
  | 'physicalMappingsChanged'
  | 'localMapAvailable'
  | 'openEditor'
  | 'calculationPending'
> {
  autoApplyingRecognition?: boolean;
  onImport: () => void;
  onPlan: () => void;
  mapReady: boolean;
  cadPreview?: boolean;
  canUndoMappings?: boolean;
  undoMappings?: () => void;
  onNeedsReview?: (section: string) => void;
  status?: ReactNode;
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
    | 'capturePreparationNeeded'
    | 'prepareCapture'
    | 'saveMutation'
    | 'mappingsChanged'
    | 'physicalMappingsChanged'
    | 'localMapAvailable'
    | 'openEditor'
    | 'calculationPending'
  > & {
    autoApplyingRecognition?: boolean;
    cadPreview?: boolean;
    canUndoMappings?: boolean;
    undoMappings?: () => void;
  },
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
  capturePreparationNeeded: state.capturePreparationNeeded,
  prepareCapture: state.prepareCapture,
  mappingsChanged: state.mappingsChanged,
  physicalMappingsChanged: state.physicalMappingsChanged,
  localMapAvailable: state.localMapAvailable,
  openEditor: state.openEditor,
  autoApplyingRecognition: state.autoApplyingRecognition,
  calculationPending: state.calculationPending,
  canUndoMappings: state.canUndoMappings,
  undoMappings: state.undoMappings,
});
