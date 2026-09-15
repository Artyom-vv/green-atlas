import type { SourcePreparationState } from '../model/sourcePreparation';
export interface SourceReadOnlyDocumentProps extends Pick<
  SourcePreparationState,
  | 'dataPassportQuery'
  | 'layers'
  | 'mappings'
  | 'setMappings'
  | 'sourceWarnings'
  | 'downloadSource'
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
  >,
) => ({
  dataPassportQuery: state.dataPassportQuery,
  layers: state.layers,
  mappings: state.mappings,
  setMappings: state.setMappings,
  sourceWarnings: state.sourceWarnings,
  downloadSource: state.downloadSource,
});
