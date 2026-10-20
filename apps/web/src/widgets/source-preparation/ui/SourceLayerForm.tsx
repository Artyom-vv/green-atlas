import { useEffect, useRef, useState, type FC } from 'react';
import { useQuery } from '@tanstack/react-query';
import { projectSourceIdentity } from '@/entities/project/model/useProjectWrite';
import { sourceDataPassportQuery } from '@/features/source-preparation/api/preparationApi';
import { LayerMappingWorkspace } from '@/entities/source-data/ui/LayerMappingWorkspace';
import {
  requiresLayerRoleReview,
  toLayerMapping,
} from '@/entities/source-data/model/layerKinds';
import { PassportLoadState } from '@/entities/source-data/ui/passport/PassportLoadState';
import { SourceGeometryReview } from './SourceGeometryReview';
import { DataPassportPanel } from '@/entities/source-data/ui/DataPassportPanel';
import { ProjectConflictNotice } from '@/entities/project/ui/ProjectConflictNotice';
import { isProjectConflict } from '@/entities/project/model/projectConflict';
import { SourcePreparationError } from './SourcePreparationError';
import type { SourceLayerFormProps } from './SourceLayerForm.props';
import { SourceReviewLayout } from './SourceReviewLayout';
export const SourceLayerForm: FC<SourceLayerFormProps> = ({
  sourceWarnings,
  incompleteConstraintLayers,
  partialAccepted,
  hasPlanningBoundary,
  reviewOnly,
  preparationBlocked,
  layers,
  mappings,
  setMappings,
  mutationError,
  reloadAfterConflict,
  projectQuery,
  nativeAreaProposals,
  decideNativeArea,
  layerRecognition,
  automaticAcceptancePending,
  initialSection,
  reviewRequest,
  status,
  recognitionProgress,
  footer,
  intro,
}) => {
  const hasBoundaryCandidates = layers.some(
    (layer) => layer.boundary_candidate?.status === 'usable',
  );
  const selectedBoundaryNeedsReview = layers.some(
    (layer) =>
      mappings[layer.id]?.kind === 'site_border' &&
      layer.boundary_candidate &&
      layer.boundary_candidate.status !== 'usable',
  );
  // Keep protocol section IDs stable; the visible navigation has three entries.
  const defaultSection =
    selectedBoundaryNeedsReview ||
    (!hasPlanningBoundary && hasBoundaryCandidates)
      ? '02'
      : !partialAccepted && incompleteConstraintLayers.length
        ? '02'
        : '03';
  const [activeSection, setActiveSection] = useState<string>(
    initialSection ?? defaultSection,
  );
  const [boundaryOpen, setBoundaryOpen] = useState(
    !hasPlanningBoundary || selectedBoundaryNeedsReview,
  );
  const navigation = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!reviewRequest) return;
    const target =
      reviewRequest.section === '01' ? '02' : reviewRequest.section;
    setActiveSection(target);
    if (reviewRequest.section === '01') setBoundaryOpen(true);
    navigation.current
      ?.querySelector<HTMLButtonElement>(`[data-section="${target}"]`)
      ?.focus();
  }, [reviewRequest]);
  const sections = [
    {
      id: '02',
      label: 'Геометрия',
      title: 'Проверьте геометрию',
      description: intro ?? 'Контуры и полнота чертежа',
    },
    {
      id: '03',
      label: 'Слои',
      title: 'Проверьте слои',
      description: intro ?? 'Проверьте назначения слоёв',
    },
    {
      id: '04',
      label: 'Сведения',
      title: 'Паспорт исходных данных',
      description: intro ?? 'Полнота исходных данных',
    },
  ];
  const dataPassportQuery = useQuery(
    sourceDataPassportQuery(projectQuery.data, activeSection === '04'),
  );
  const hasPendingMappings = layers.some((layer) =>
    requiresLayerRoleReview(mappings[layer.id] ?? toLayerMapping(layer)),
  );
  return (
    <SourceReviewLayout
      sections={sections}
      activeSection={activeSection}
      onSectionChange={setActiveSection}
      navRef={navigation}
      status={status}
      footer={footer}
      headerProgress={
        activeSection === '04' && dataPassportQuery.isFetching ? (
          <div
            className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden bg-blue-100"
            aria-hidden="true"
          >
            <span className="block h-full w-[35%] animate-[progress-indeterminate_1.2s_ease-in-out_infinite] bg-blue-600 motion-reduce:animate-none" />
          </div>
        ) : undefined
      }
    >
      <section
        role="region"
        aria-label="Геометрия"
        hidden={activeSection !== '02'}
      >
        <SourceGeometryReview
          sourceWarnings={sourceWarnings}
          incompleteConstraintLayers={incompleteConstraintLayers}
          partialAccepted={partialAccepted}
          hasPlanningBoundary={hasPlanningBoundary}
          reviewOnly={reviewOnly}
          preparationBlocked={preparationBlocked}
          layers={layers}
          mappings={mappings}
          setMappings={setMappings}
          projectQuery={projectQuery}
          nativeAreaProposals={nativeAreaProposals}
          decideNativeArea={decideNativeArea}
          boundaryOpen={boundaryOpen}
          onBoundaryOpenChange={setBoundaryOpen}
        />
      </section>
      <section
        role="region"
        aria-label="Слои"
        hidden={activeSection !== '03'}
        data-expanded={activeSection === '03'}
      >
        {recognitionProgress}
        <LayerMappingWorkspace
          key={projectSourceIdentity(projectQuery.data)}
          recognition={layerRecognition}
          automaticAcceptancePending={automaticAcceptancePending}
          readOnly={reviewOnly}
          disabled={preparationBlocked}
          layers={layers}
          mappings={mappings}
          onChange={setMappings}
          informationInHeader
          defaultView={reviewOnly || !hasPendingMappings ? 'list' : 'review'}
          onBoundaryReview={() => {
            setActiveSection('02');
            setBoundaryOpen(true);
          }}
        />
      </section>
      <section
        role="region"
        aria-label="Сведения"
        hidden={activeSection !== '04'}
      >
        {dataPassportQuery.isFetching && !dataPassportQuery.data ? (
          <PassportLoadState state="loading" />
        ) : dataPassportQuery.isError ? (
          <PassportLoadState
            state="error"
            onRetry={() => void dataPassportQuery.refetch()}
          />
        ) : dataPassportQuery.data ? (
          <DataPassportPanel passport={dataPassportQuery.data} header={null} />
        ) : null}
      </section>
      {!!mutationError &&
        (isProjectConflict(mutationError) ? (
          <ProjectConflictNotice
            error={mutationError}
            onReload={() => void reloadAfterConflict()}
            reloading={projectQuery.isFetching}
          />
        ) : (
          <SourcePreparationError
            error={mutationError}
            layers={layers}
            mappings={mappings}
          />
        ))}
    </SourceReviewLayout>
  );
};
