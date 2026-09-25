import type { SourcePreparationState } from '../model/sourcePreparation';
export interface SourceReadOnlyDocumentProps extends Pick<
  SourcePreparationState,
  | 'dataPassportQuery'
  | 'layers'
  | 'mappings'
  | 'setMappings'
  | 'sourceWarnings'
  | 'downloadSource'
  | 'startSourceEditing'
  | 'reviewOnly'
  | 'layerRecognition'
  | 'projectQuery'
> {
  onPlan: () => void;
}
export const SourceReadOnlyDocumentPropsFor = (
  state: Pick<
    SourcePreparationState,
    | 'dataPassportQuery'
    | 'layers'
    | 'mappings'
    | 'setMappings'
    | 'sourceWarnings'
    | 'downloadSource'
    | 'startSourceEditing'
    | 'reviewOnly'
    | 'layerRecognition'
    | 'projectQuery'
  >,
) => ({
  dataPassportQuery: state.dataPassportQuery,
  layers: state.layers,
  mappings: state.mappings,
  setMappings: state.setMappings,
  sourceWarnings: state.sourceWarnings,
  downloadSource: state.downloadSource,
  startSourceEditing: state.startSourceEditing,
  reviewOnly: state.reviewOnly,
  layerRecognition: state.layerRecognition,
  projectQuery: state.projectQuery,
});
