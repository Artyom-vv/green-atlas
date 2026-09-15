import type { SourcePreparationState } from '../model/sourcePreparation';
export interface SourceLayerFormProps extends Pick<
  SourcePreparationState,
  | 'sourceWarnings'
  | 'incompleteConstraintLayers'
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
> {}
export const SourceLayerFormPropsFor = (
  state: Pick<
    SourcePreparationState,
    | 'sourceWarnings'
    | 'incompleteConstraintLayers'
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
  >,
) => ({
  sourceWarnings: state.sourceWarnings,
  incompleteConstraintLayers: state.incompleteConstraintLayers,
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
});
