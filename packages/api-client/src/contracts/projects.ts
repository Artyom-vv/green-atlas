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

export type PlantingZoneAssignment = Schemas['PlantingZoneAssignment'];

export type PlantingZonePreview = Schemas['PlantingZonePreview'];

export type ZoneChangePreview = Schemas['ZoneChangePreview'];

export type ProjectOperation = Schemas['ProjectOperation'];

export type OperationKind = Schemas['OperationKind'];

export type GeometrySnapshot = Schemas['GeometrySnapshot'];
