// Сгенерировано из OpenAPI. Общие типы принадлежат контракту API.
import type { WireSchema } from './contracts/wire';
import type { paths } from './schema';
export type AcceptPartialGeometryRequest =
  WireSchema<'AcceptPartialGeometryRequest'>;
export type AppliedLocation = WireSchema<'AppliedLocation'>;
export type AssortmentEntry = WireSchema<'AssortmentEntry'>;
export type AssortmentInventory = WireSchema<'AssortmentInventory'>;
export type AutomaticPlacementPreview = WireSchema<'AutomaticPlacementPreview'>;
export type { AutomaticPlacementRequest } from './contracts/planning';
export type AxisDistanceEvidence = WireSchema<'AxisDistanceEvidence'>;
export type AxisSourceReference = WireSchema<'AxisSourceReference'>;
export type BodyUploadAutocadLiveApiProjectsProjectIdSourceAutocadLivePost =
  WireSchema<'Body_upload_autocad_live_api_projects__project_id__source_autocad_live_post'>;
export type BoundaryCandidate = WireSchema<'BoundaryCandidate'>;
export type BoundaryCandidateStatus = WireSchema<'BoundaryCandidateStatus'>;
export type BrushPreview = WireSchema<'BrushPreview'>;
export type { BrushPreviewRequest } from './contracts/planning';
export type BrushStroke = WireSchema<'BrushStroke'>;
export type { BuildingScreenRequest } from './contracts/planning';
export type BuildingScreenTargets = WireSchema<'BuildingScreenTargets'>;
export type CadBoundarySelection = WireSchema<'CadBoundarySelection'>;
export type CadDrawingEntry = WireSchema<'CadDrawingEntry'>;
export type CadDrawingPassport = WireSchema<'CadDrawingPassport'>;
export type CadDrawingSelection = WireSchema<'CadDrawingSelection'>;
export type CadDrawingSnapshotSelection =
  WireSchema<'CadDrawingSnapshotSelection'>;
export type CadIntakeRecord = WireSchema<'CadIntakeRecord'>;
export type CadIntakeRequest = WireSchema<'CadIntakeRequest'>;
export type CadOpeningReview = WireSchema<'CadOpeningReview'>;
export type CadPackagePassport = WireSchema<'CadPackagePassport'>;
export type CadPrepareRecord = WireSchema<'CadPrepareRecord'>;
export type CadPrepareRequest = WireSchema<'CadPrepareRequest'>;
export type CadPrepareResult = WireSchema<'CadPrepareResult'>;
export type CadPreviewProvenance = WireSchema<'CadPreviewProvenance'>;
export type CadPreviewRecord = WireSchema<'CadPreviewRecord'>;
export type CadPreviewRequest = WireSchema<'CadPreviewRequest'>;
export type CadPreviewResult = WireSchema<'CadPreviewResult'>;
export type CadReferenceOverride = WireSchema<'CadReferenceOverride'>;
export type CadReferencePassport = WireSchema<'CadReferencePassport'>;
export type CadSkippedReference = WireSchema<'CadSkippedReference'>;
export type CadSnapshotDependency = WireSchema<'CadSnapshotDependency'>;
export type CadSnapshotProvenance = WireSchema<'CadSnapshotProvenance'>;
export type CadSnapshotSelection = WireSchema<'CadSnapshotSelection'>;
export type CadSourceAsset = WireSchema<'CadSourceAsset'>;
export type CandidateReasonSummary = WireSchema<'CandidateReasonSummary'>;
export type ChangeSetCandidateResult = WireSchema<'ChangeSetCandidateResult'>;
export type ChangeSetPreview = WireSchema<'ChangeSetPreview'>;
export type CoordinateReference = WireSchema<'CoordinateReference'>;
export type DataPassport = WireSchema<'DataPassport'>;
export type DataPassportEntry = WireSchema<'DataPassportEntry'>;
export type DrawingBoundaryCandidate = WireSchema<'DrawingBoundaryCandidate'>;
export type DrawingBoundaryCatalog = WireSchema<'DrawingBoundaryCatalog'>;
export type DrawingInspection = WireSchema<'DrawingInspection'>;
export type DxfTextEncoding = WireSchema<'DxfTextEncoding'>;
export type DxfVerticalPrimitive = WireSchema<'DxfVerticalPrimitive'>;
export type EffectEstimate = WireSchema<'EffectEstimate'>;
export type EvidenceAssessment = WireSchema<'EvidenceAssessment'>;
export type ExportArtifact = WireSchema<'ExportArtifact'>;
export type { FillPatternRequest } from './contracts/planning';
export type GeometryCoverageIssue = WireSchema<'GeometryCoverageIssue'>;
export type GeometryEvidence = WireSchema<'GeometryEvidence'>;
export type GeometryEvidenceItem = WireSchema<'GeometryEvidenceItem'>;
export type GeometrySnapshot = WireSchema<'GeometrySnapshot'>;
export type GrowthEnvelopeForecast = WireSchema<'GrowthEnvelopeForecast'>;
export type GrowthForecastResponse = WireSchema<'GrowthForecastResponse'>;
export type HTTPValidationError = WireSchema<'HTTPValidationError'>;
export type ImportEditability = WireSchema<'ImportEditability'>;
export type ImportMode = WireSchema<'ImportMode'>;
export type ImportStatus = WireSchema<'ImportStatus'>;
export type LabelObservation = WireSchema<'LabelObservation'>;
export type Layer = WireSchema<'Layer'>;
export type LayerCategory = WireSchema<'LayerCategory'>;
export type LayerCategoryOption = WireSchema<'LayerCategoryOption'>;
export type LayerGeometryCoverage = WireSchema<'LayerGeometryCoverage'>;
export type LayerKind = WireSchema<'LayerKind'>;
export type LayerMapping = WireSchema<'LayerMapping'>;
export type LayerMappingRequest = WireSchema<'LayerMappingRequest'>;
export type LayerRecognition = WireSchema<'LayerRecognition'>;
export type LayerRoleProposal = WireSchema<'LayerRoleProposal'>;
export type LayerSemanticEvidence = WireSchema<'LayerSemanticEvidence'>;
export type LayerSuggestionConfidence = WireSchema<'LayerSuggestionConfidence'>;
export type LiveCadReference = WireSchema<'LiveCadReference'>;
export type LiveDocumentCapture = WireSchema<'LiveDocumentCapture'>;
export type LiveSession = WireSchema<'LiveSession'>;
export type LocationAlignment = WireSchema<'LocationAlignment'>;
export type LocationAlignmentRequest = WireSchema<'LocationAlignmentRequest'>;
export type LocationApplyRequest = WireSchema<'LocationApplyRequest'>;
export type LocationControlPair = WireSchema<'LocationControlPair'>;
export type LocationDraft = WireSchema<'LocationDraft'>;
export type LocationResolveRequest = WireSchema<'LocationResolveRequest'>;
export type LocationServiceSettings = WireSchema<'LocationServiceSettings'>;
export type LocationServiceSettingsUpdate =
  WireSchema<'LocationServiceSettingsUpdate'>;
export type LocationState = WireSchema<'LocationState'>;
export type LocationSuggestion = WireSchema<'LocationSuggestion'>;
export type LocationSuggestions = WireSchema<'LocationSuggestions'>;
export type LocationSuggestionsRequest =
  WireSchema<'LocationSuggestionsRequest'>;
export type LocationTransform = WireSchema<'LocationTransform'>;
export type NativeAreaPreview = WireSchema<'NativeAreaPreview'>;
export type NativeAreaProposalDecisionRequest =
  WireSchema<'NativeAreaProposalDecisionRequest'>;
export type NativeAreaProposalReview = WireSchema<'NativeAreaProposalReview'>;
export type NativeDxfSourceAsset = WireSchema<'NativeDxfSourceAsset'>;
export type NativeFaceDecision = WireSchema<'NativeFaceDecision'>;
export type NativeFaceReview = WireSchema<'NativeFaceReview'>;
export type NativeFaceReviewItem = WireSchema<'NativeFaceReviewItem'>;
export type OperationError = WireSchema<'OperationError'>;
export type OperationKind = WireSchema<'OperationKind'>;
export type OperationStatus = WireSchema<'OperationStatus'>;
export type PatternPreview = WireSchema<'PatternPreview'>;
export type PatternSkippedCandidate = WireSchema<'PatternSkippedCandidate'>;
export type PatternZoneAllocation = WireSchema<'PatternZoneAllocation'>;
export type PlacementCheck = WireSchema<'PlacementCheck'>;
export type { PlacementCheckRequest } from './contracts/planning';
export type PlacementMaskPreset = WireSchema<'PlacementMaskPreset'>;
export type { PlacementMaskRequest } from './contracts/planning';
export type Plan = WireSchema<'Plan'>;
export type PlanChangeReceipt = WireSchema<'PlanChangeReceipt'>;
export type PlanChangeReceiptResult = WireSchema<'PlanChangeReceiptResult'>;
export type PlanChangeSetApplyRequest = WireSchema<'PlanChangeSetApplyRequest'>;
export type { PlanChangeSetDraft } from './contracts/planning';
export type PlanHistoryEntry = WireSchema<'PlanHistoryEntry'>;
export type PlanHistoryState = WireSchema<'PlanHistoryState'>;
export type PlanMutationResult = WireSchema<'PlanMutationResult'>;
export type PlanObject = WireSchema<'PlanObject'>;
export type PlanObjectAddOperation = WireSchema<'PlanObjectAddOperation'>;
export type { PlanObjectCreate } from './contracts/planning';
export type PlanObjectDeleteOperation = WireSchema<'PlanObjectDeleteOperation'>;
export type PlanObjectUpdate = WireSchema<'PlanObjectUpdate'>;
export type PlanObjectUpdateOperation = WireSchema<'PlanObjectUpdateOperation'>;
export type PlanObjectsDeleteRequest = WireSchema<'PlanObjectsDeleteRequest'>;
export type PlanValidationBasis = WireSchema<'PlanValidationBasis'>;
export type PlantingRuleTrace = WireSchema<'PlantingRuleTrace'>;
export type PlantingZoneAssignment = WireSchema<'PlantingZoneAssignment'>;
export type PlantingZoneOverlap = WireSchema<'PlantingZoneOverlap'>;
export type PlantingZonePreview = WireSchema<'PlantingZonePreview'>;
export type PlantingZonesRequest = WireSchema<'PlantingZonesRequest'>;
export type PreparedAoiDrawingProvenance =
  WireSchema<'PreparedAoiDrawingProvenance'>;
export type PreparedAoiProvenance = WireSchema<'PreparedAoiProvenance'>;
export type PreparedDrawingProvenance = WireSchema<'PreparedDrawingProvenance'>;
export type PreparedSourceProvenance = WireSchema<'PreparedSourceProvenance'>;
export type Project = WireSchema<'Project'>;
export type ProjectCreate = WireSchema<'ProjectCreate'>;
export type ProjectOperation = WireSchema<'ProjectOperation'>;
export type ProjectRead = WireSchema<'ProjectRead'>;
export type ProjectStatus = WireSchema<'ProjectStatus'>;
export type ProjectSummary = WireSchema<'ProjectSummary'>;
export type RecommendationExplanation = WireSchema<'RecommendationExplanation'>;
export type RecommendationPreview = WireSchema<'RecommendationPreview'>;
export type { RecommendationRequest } from './contracts/planning';
export type RegulatoryReleaseBasis = WireSchema<'RegulatoryReleaseBasis'>;
export type ReleaseArtifact = WireSchema<'ReleaseArtifact'>;
export type ReleaseCreateRequest = WireSchema<'ReleaseCreateRequest'>;
export type ReleasePackage = WireSchema<'ReleasePackage'>;
export type RenderCamera = WireSchema<'RenderCamera'>;
export type RenderPreparationPreview = WireSchema<'RenderPreparationPreview'>;
export type RenderPreparationSummary = WireSchema<'RenderPreparationSummary'>;
export type RenderPreviewAsset = WireSchema<'RenderPreviewAsset'>;
export type RenderPreviewAssetBounds = WireSchema<'RenderPreviewAssetBounds'>;
export type RenderPreviewCurb = WireSchema<'RenderPreviewCurb'>;
export type RenderPreviewMaterial = WireSchema<'RenderPreviewMaterial'>;
export type RenderPreviewMaterialMaps = WireSchema<'RenderPreviewMaterialMaps'>;
export type RenderPreviewMesh = WireSchema<'RenderPreviewMesh'>;
export type RenderPreviewTree = WireSchema<'RenderPreviewTree'>;
export type ReviewedAreaMember = WireSchema<'ReviewedAreaMember'>;
export type { RowPatternRequest } from './contracts/planning';
export type RuleSourceFeature = WireSchema<'RuleSourceFeature'>;
export type RuleTraceBasis = WireSchema<'RuleTraceBasis'>;
export type RuleTraceEntry = WireSchema<'RuleTraceEntry'>;
export type SceneContextFeature = WireSchema<'SceneContextFeature'>;
export type SceneEvidence = WireSchema<'SceneEvidence'>;
export type ScenePlantObject = WireSchema<'ScenePlantObject'>;
export type SceneRenderJob = WireSchema<'SceneRenderJob'>;
export type SceneRenderPreparation = WireSchema<'SceneRenderPreparation'>;
export type SceneRenderPreparationRequest =
  WireSchema<'SceneRenderPreparationRequest'>;
export type SceneRenderRequest = WireSchema<'SceneRenderRequest'>;
export type SceneSnapshot = WireSchema<'SceneSnapshot'>;
export type SceneVerticalPrimitive = WireSchema<'SceneVerticalPrimitive'>;
export type SceneView = WireSchema<'SceneView'>;
export type SearchDomain = WireSchema<'SearchDomain'>;
export type SourceAreaGroup = WireSchema<'SourceAreaGroup'>;
export type SourceAreaGroupCheck = WireSchema<'SourceAreaGroupCheck'>;
export type SourceAreaGroupRequest = WireSchema<'SourceAreaGroupRequest'>;
export type SourceContextObject = WireSchema<'SourceContextObject'>;
export type SourceFile = WireSchema<'SourceFile'>;
export type SourceIdentity = WireSchema<'SourceIdentity'>;
export type SourceObjectContext = WireSchema<'SourceObjectContext'>;
export type SourceObjectDecision = WireSchema<'SourceObjectDecision'>;
export type SourceObjectDecisionRequest =
  WireSchema<'SourceObjectDecisionRequest'>;
export type SourceObjectReviewItem = WireSchema<'SourceObjectReviewItem'>;
export type SourceObjectReviewPage = WireSchema<'SourceObjectReviewPage'>;
export type SourceReadIssue = WireSchema<'SourceReadIssue'>;
export type SourceReadIssues = WireSchema<'SourceReadIssues'>;
export type SourceReview = WireSchema<'SourceReview'>;
export type SourceReviewIssue = WireSchema<'SourceReviewIssue'>;
export type SpeciesRevision = WireSchema<'SpeciesRevision'>;
export type SpeciesShortlistItem = WireSchema<'SpeciesShortlistItem'>;
export type SpeciesShortlistRequest = WireSchema<'SpeciesShortlistRequest'>;
export type StreetCandidate = WireSchema<'StreetCandidate'>;
export type TopologyRecipe = WireSchema<'TopologyRecipe'>;
export type UtilityAxisBinding = WireSchema<'UtilityAxisBinding'>;
export type UtilityContext = WireSchema<'UtilityContext'>;
export type UtilityGeometryReference = WireSchema<'UtilityGeometryReference'>;
export type UtilityType = WireSchema<'UtilityType'>;
export type ValidationError = WireSchema<'ValidationError'>;
export type ValidationIssue = WireSchema<'ValidationIssue'>;
export type GetSpeciesCatalogParams = NonNullable<
  paths['/api/species']['get']['parameters']['query']
>;
export type GetAssortmentCatalogParams = NonNullable<
  paths['/api/species/assortment']['get']['parameters']['query']
>;
export type GetProjectParams = NonNullable<
  paths['/api/projects/{project_id}']['get']['parameters']['query']
>;
export type SourceDxfDownloadParams = NonNullable<
  paths['/api/projects/{project_id}/source-dxf/download']['get']['parameters']['query']
>;
export type SourceObjectReviewParams = NonNullable<
  paths['/api/projects/{project_id}/source-object-review']['get']['parameters']['query']
>;
export type SourceObjectContextParams = NonNullable<
  paths['/api/projects/{project_id}/source-object-context']['get']['parameters']['query']
>;
export type NativeAreaPreviewParams = NonNullable<
  paths['/api/projects/{project_id}/source-native-area/preview']['get']['parameters']['query']
>;
export type GetMapFeaturesParams = NonNullable<
  paths['/api/projects/{project_id}/map-features']['get']['parameters']['query']
>;
export type GetLatestOperationParams = NonNullable<
  paths['/api/projects/{project_id}/operations/latest']['get']['parameters']['query']
>;
export type GetPlanGrowthForecastParams = NonNullable<
  paths['/api/projects/{project_id}/plan/growth-forecast']['get']['parameters']['query']
>;
export type ReadPlanChangeReceiptParams = NonNullable<
  paths['/api/projects/{project_id}/plan/change-sets/{preview_id}/receipt']['get']['parameters']['query']
>;
export type BuildingScreenTargetsParams = NonNullable<
  paths['/api/projects/{project_id}/building-screen/targets']['get']['parameters']['query']
>;
export type GetPlanSceneParams = NonNullable<
  paths['/api/projects/{project_id}/plan/scene']['get']['parameters']['query']
>;
export type GetSceneViewParams = NonNullable<
  paths['/api/projects/{project_id}/scene-view']['get']['parameters']['query']
>;
export type GetSpeciesCatalog200 =
  paths['/api/species']['get']['responses'][200]['content']['application/json'];
export type Health200 =
  paths['/api/health']['get']['responses'][200]['content']['application/json'];
export type ListProjects200 =
  paths['/api/projects']['get']['responses'][200]['content']['application/json'];
export type SourceLayerReviewStatus200 =
  paths['/api/projects/{project_id}/source-layer-review/status']['get']['responses'][200]['content']['application/json'];
export type SourceDxfDownload200 =
  paths['/api/projects/{project_id}/source-dxf/download']['get']['responses'][200]['content']['application/json'];
export type GetLatestOperation200 =
  paths['/api/projects/{project_id}/operations/latest']['get']['responses'][200]['content']['application/json'];
export type GetPlanPlacementMasks200 =
  paths['/api/projects/{project_id}/plan/placement-masks']['get']['responses'][200]['content']['application/json'];
export type ShortlistProjectSpecies200 =
  paths['/api/projects/{project_id}/species/shortlist']['post']['responses'][200]['content']['application/json'];
export type DownloadExport200 =
  paths['/api/projects/{project_id}/exports/{artifact_id}/download']['get']['responses'][200]['content']['application/json'];
export type DownloadReleaseArtifact200 =
  paths['/api/projects/{project_id}/releases/{release_id}/artifacts/{artifact_id}']['get']['responses'][200]['content']['application/json'];
export type SceneRenderImage200 =
  paths['/api/projects/{project_id}/scene-renders/{job_id}/image']['get']['responses'][200]['content']['application/json'];
export type SceneRenderReceipt200 =
  paths['/api/projects/{project_id}/scene-renders/{job_id}/receipt']['get']['responses'][200]['content']['application/json'];
