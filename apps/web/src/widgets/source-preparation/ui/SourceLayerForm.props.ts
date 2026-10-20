import type { ReactNode } from 'react';
import type { SourcePreparationState } from '../model/sourcePreparation';
export interface SourceLayerFormProps extends Pick<
  SourcePreparationState,
  | 'sourceWarnings'
  | 'unconfirmedMappings'
  | 'incompleteConstraintLayers'
  | 'partialAccepted'
  | 'acceptPartialGeometry'
  | 'hasPlanningBoundary'
  | 'reviewOnly'
  | 'preparationBlocked'
  | 'layers'
  | 'mappings'
  | 'setMappings'
  | 'mutationError'
  | 'reloadAfterConflict'
  | 'projectQuery'
  | 'nativeAreaProposals'
  | 'decideNativeArea'
  | 'layerRecognition'
  | 'automaticAcceptancePending'
> {
  initialSection?: '02' | '03' | '04';
  reviewRequest?: { section: string; sequence: number };
  status?: ReactNode;
  recognitionProgress?: ReactNode;
  footer?: ReactNode;
  intro?: string;
}
export const SourceLayerFormPropsFor = (
  state: Pick<
    SourcePreparationState,
    | 'sourceWarnings'
    | 'unconfirmedMappings'
    | 'incompleteConstraintLayers'
    | 'partialAccepted'
    | 'acceptPartialGeometry'
    | 'hasPlanningBoundary'
    | 'reviewOnly'
    | 'preparationBlocked'
    | 'layers'
    | 'mappings'
    | 'setMappings'
    | 'mutationError'
    | 'reloadAfterConflict'
    | 'projectQuery'
    | 'nativeAreaProposals'
    | 'decideNativeArea'
    | 'layerRecognition'
    | 'automaticAcceptancePending'
  >,
) => ({
  sourceWarnings: state.sourceWarnings,
  unconfirmedMappings: state.unconfirmedMappings,
  incompleteConstraintLayers: state.incompleteConstraintLayers,
  partialAccepted: state.partialAccepted,
  acceptPartialGeometry: state.acceptPartialGeometry,
  hasPlanningBoundary: state.hasPlanningBoundary,
  reviewOnly: state.reviewOnly,
  preparationBlocked: state.preparationBlocked,
  layers: state.layers,
  mappings: state.mappings,
  setMappings: state.setMappings,
  automaticAcceptancePending: state.automaticAcceptancePending,
  mutationError: state.mutationError,
  reloadAfterConflict: state.reloadAfterConflict,
  projectQuery: state.projectQuery,
  nativeAreaProposals: state.nativeAreaProposals,
  decideNativeArea: state.decideNativeArea,
  layerRecognition: state.layerRecognition,
});
