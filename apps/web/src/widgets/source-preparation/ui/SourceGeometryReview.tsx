import { Disclosure } from '@green/ui';
import type { ReactNode } from 'react';
import {
  BoundaryCandidateInformation,
  BoundaryCandidatePicker,
} from '@/entities/source-data/ui/BoundaryCandidatePicker';
import { errorMessage } from '@/shared/errors/errorMessage';
import type { SourcePreparationState } from '../model/sourcePreparation';
import { NativeAreaReview } from './NativeAreaReview';
import { NativeFaceReview } from './NativeFaceReview';
import {
  SourceObjectReview,
  canReviewSourceObjects,
} from './SourceObjectReview';
import { SourceReadIssues } from './SourceReadIssues';

export interface SourceGeometryReviewProps extends Pick<
  SourcePreparationState,
  | 'sourceWarnings'
  | 'incompleteConstraintLayers'
  | 'partialAccepted'
  | 'hasPlanningBoundary'
  | 'reviewOnly'
  | 'preparationBlocked'
  | 'layers'
  | 'mappings'
  | 'setMappings'
  | 'projectQuery'
  | 'nativeAreaProposals'
  | 'decideNativeArea'
> {
  boundaryOpen: boolean;
  onBoundaryOpenChange: (open: boolean) => void;
}

function GeometryGroup({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section aria-label={title} className="grid min-w-0 gap-2">
      <h2 className="m-0 px-4 text-sm leading-5 font-medium text-neutral-500 min-[720px]:px-6">
        {title}
      </h2>
      <div className="min-w-0 [&>*:last-child]:border-b-0">{children}</div>
    </section>
  );
}

/** Each group owns one kind of geometry work and its contextual details. */
export function SourceGeometryReview({
  sourceWarnings,
  incompleteConstraintLayers,
  partialAccepted,
  hasPlanningBoundary,
  reviewOnly,
  preparationBlocked,
  layers,
  mappings,
  setMappings,
  projectQuery,
  nativeAreaProposals,
  decideNativeArea,
  boundaryOpen,
  onBoundaryOpenChange,
}: SourceGeometryReviewProps) {
  const project = projectQuery.data;
  const hasBoundaryCandidates = layers.some(
    (layer) => layer.boundary_candidate?.status === 'usable',
  );
  const boundaryIssues = layers.filter(
    (layer) =>
      layer.boundary_candidate?.status === 'invalid' &&
      layer.boundary_candidate.issue,
  );
  const selectedBoundaryNeedsReview = layers.some(
    (layer) =>
      mappings[layer.id]?.kind === 'site_border' &&
      layer.boundary_candidate &&
      layer.boundary_candidate.status !== 'usable',
  );
  const hasObjectTasks =
    nativeAreaProposals.length > 0 ||
    Boolean(project?.source_file?.native_session) ||
    (!reviewOnly && Boolean(project && canReviewSourceObjects(project)));
  const hasSourceTasks =
    sourceWarnings.length > 0 ||
    Boolean(project?.source_file?.cad_snapshot_provenance?.live_capture) ||
    (!reviewOnly && incompleteConstraintLayers.length > 0);
  return (
    <div className="grid min-w-0 gap-6 text-sm leading-5">
      {!reviewOnly &&
        (hasBoundaryCandidates ||
          boundaryIssues.length > 0 ||
          selectedBoundaryNeedsReview) && (
          <Disclosure
            variant="section"
            title="Граница расчёта"
            actions={<BoundaryCandidateInformation layers={layers} />}
            open={boundaryOpen}
            onOpenChange={onBoundaryOpenChange}
            contentVariant="flush"
          >
            <BoundaryCandidatePicker
              readOnly={preparationBlocked}
              requireAttestation={
                project?.import_status?.mode === 'autocad_live'
              }
              layers={layers}
              mappings={mappings}
              onChange={setMappings}
            />
            {!!boundaryIssues.length && (
              <Disclosure
                variant="plain"
                title={`Границы требуют проверки (${boundaryIssues.length})`}
                className="px-6 pb-4 max-[719px]:px-4"
              >
                <p className="m-0 text-neutral-600">
                  Эти контуры не приняты за территорию расчёта
                </p>
                <dl className="m-0 grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-x-4 gap-y-2 max-[719px]:grid-cols-1">
                  {boundaryIssues.map((layer) => (
                    <div key={layer.id} className="contents">
                      <dt className="wrap-anywhere">{layer.source_name}</dt>
                      <dd className="m-0 wrap-anywhere text-neutral-600">
                        {layer.boundary_candidate?.issue}
                      </dd>
                    </div>
                  ))}
                </dl>
                <p className="m-0 text-neutral-600">
                  Рабочую область можно задать на карте с сохранением
                  подтверждённых ограничений
                </p>
              </Disclosure>
            )}
          </Disclosure>
        )}
      {!reviewOnly &&
        !hasPlanningBoundary &&
        !hasBoundaryCandidates &&
        !boundaryIssues.length && (
          <Disclosure variant="section" title="Граница расчёта не задана">
            <p className="m-0 text-neutral-600">
              После подготовки карты рабочую область можно задать вручную с
              сохранением подтверждённых ограничений
            </p>
          </Disclosure>
        )}
      {hasObjectTasks && (
        <GeometryGroup title="Геометрия объектов">
          <NativeAreaReview
            projectId={project?.id ?? ''}
            sourceSha256={project?.source_file?.content_sha256 ?? ''}
            proposals={nativeAreaProposals}
            automatic={!!project?.source_file?.native_session}
            readOnly={reviewOnly}
            busy={preparationBlocked}
            saving={decideNativeArea.isPending}
            error={
              decideNativeArea.error
                ? errorMessage(decideNativeArea.error)
                : undefined
            }
            onRefresh={() => {
              void projectQuery
                .refetch({ throwOnError: true })
                .then(() => decideNativeArea.reset())
                .catch(() => {
                  /* Keep an unconfirmed decision visible after a failed read. */
                });
            }}
            onDecision={(proposal, decision) =>
              decideNativeArea.mutate({
                proposalId: proposal.id,
                proposalSha256: proposal.proposal_sha256,
                decision,
              })
            }
          />
          {project && (
            <NativeFaceReview project={project} disabled={preparationBlocked} />
          )}
          {!reviewOnly && project && (
            <SourceObjectReview
              project={project}
              disabled={preparationBlocked}
            />
          )}
        </GeometryGroup>
      )}
      {hasSourceTasks && (
        <GeometryGroup title="Полнота исходника">
          {!!sourceWarnings.length && (
            <Disclosure
              variant="section"
              title={`Замечания к файлу (${sourceWarnings.length})`}
            >
              <ul className="m-0 grid list-none gap-3 p-0 text-neutral-600">
                {sourceWarnings.map((warning) => (
                  <li className="wrap-anywhere" key={warning}>
                    {warning}
                  </li>
                ))}
              </ul>
            </Disclosure>
          )}
          {project?.source_file?.cad_snapshot_provenance?.live_capture && (
            <SourceReadIssues
              projectId={project.id ?? ''}
              sourceSha={project.source_file.content_sha256 ?? ''}
            />
          )}
          {!reviewOnly && !!incompleteConstraintLayers.length && (
            <Disclosure
              variant="section"
              title={
                <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  <span className="text-warning-strong">
                    Часть геометрии недоступна (
                    {incompleteConstraintLayers.length})
                  </span>
                  {partialAccepted && (
                    <span className="font-normal text-neutral-500">Учтено</span>
                  )}
                </span>
              }
            >
              <p className="m-0 text-neutral-600">
                {partialAccepted
                  ? 'Карта строится по доступным объектам'
                  : 'Для подготовки потребуется подтвердить расчёт по доступным объектам'}
              </p>
              <p className="mt-2 mb-0 text-neutral-600">
                Пропуски остаются в отчёте и не участвуют в проверках
              </p>
              <ul className="m-0 mt-3 grid list-none gap-2 p-0">
                {incompleteConstraintLayers.map((layer) => (
                  <li key={layer.id} className="wrap-anywhere">
                    {layer.source_name}
                  </li>
                ))}
              </ul>
            </Disclosure>
          )}
        </GeometryGroup>
      )}
    </div>
  );
}
