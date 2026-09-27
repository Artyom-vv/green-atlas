import type { Schemas } from './wire';

export interface ProjectWriteOptions {
  expectedStateVersion: number;
}

export type Project = Schemas['Project'];

export type ProjectSummary = Schemas['ProjectSummary'];

export type ImportStatus = Schemas['ImportStatus'];

export type SourceFile = Schemas['SourceFile'];

export type NativeDxfSourceAsset = Schemas['NativeDxfSourceAsset'];

export type Layer = Schemas['Layer'];

export type DataPassportEntry = Schemas['DataPassportEntry'];

export type DataPassport = Schemas['DataPassport'];

export type LayerKind = Schemas['LayerKind'];

export type LayerMapping = Schemas['LayerMapping'];
export type LayerCategory = Schemas['LayerCategory'];
export type LayerRecognition = Schemas['LayerRecognition'];
export type NativeAreaPreview = Schemas['NativeAreaPreview'];
export type SourceObjectReviewPage = Schemas['SourceObjectReviewPage'];
export type SourceObjectContext = Schemas['SourceObjectContext'];
export type SourceContextObject = Schemas['SourceContextObject'];
export type SourceAreaGroupRequest = Schemas['SourceAreaGroupRequest'];
export type SourceAreaGroupCheck = Schemas['SourceAreaGroupCheck'];
export type SourceReadIssues = Schemas['SourceReadIssues'];
export type NativeFaceReview = Schemas['NativeFaceReview'];
export type NativeFaceDecision = Schemas['NativeFaceDecision'];
export type SourceObjectReviewItem = Schemas['SourceObjectReviewItem'];
export type SourceObjectDecisionRequest = Schemas['SourceObjectDecisionRequest'];

export type PlantingZoneAssignment = Schemas['PlantingZoneAssignment'];

export type PlantingZonePreview = Schemas['PlantingZonePreview'];

export type ZoneChangePreview = Schemas['ZoneChangePreview'];

export type ProjectOperation = Schemas['ProjectOperation'];

export type OperationKind = Schemas['OperationKind'];

export type GeometrySnapshot = Schemas['GeometrySnapshot'];
