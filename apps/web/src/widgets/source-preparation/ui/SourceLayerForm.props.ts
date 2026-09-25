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
  | 'dataPassportQuery'
  | 'mutationError'
  | 'reloadAfterConflict'
  | 'projectQuery'
  | 'nativeAreaProposals'
  | 'decideNativeArea'
  | 'layerRecognition'
  | 'layerRecognitionQuery'
> {}
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
    | 'dataPassportQuery'
    | 'mutationError'
    | 'reloadAfterConflict'
    | 'projectQuery'
    | 'nativeAreaProposals'
    | 'decideNativeArea'
    | 'layerRecognition'
    | 'layerRecognitionQuery'
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
  dataPassportQuery: state.dataPassportQuery,
  mutationError: state.mutationError,
  reloadAfterConflict: state.reloadAfterConflict,
  projectQuery: state.projectQuery,
  nativeAreaProposals: state.nativeAreaProposals,
  decideNativeArea: state.decideNativeArea,
  layerRecognition: state.layerRecognition,
  layerRecognitionQuery: state.layerRecognitionQuery,
});
