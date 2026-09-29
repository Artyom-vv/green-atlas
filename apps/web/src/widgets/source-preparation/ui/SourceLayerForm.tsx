import type { FC } from 'react';
import { Disclosure, InlineMessage } from '@green/ui';
import { LayerMappingWorkspace } from '@/entities/source-data/ui/LayerMappingWorkspace';
import { BoundaryCandidatePicker } from '@/entities/source-data/ui/BoundaryCandidatePicker';
import { DataPassportPanel } from '@/entities/source-data/ui/DataPassportPanel';
import { ProjectConflictNotice } from '@/entities/project/ui/ProjectConflictNotice';
import { isProjectConflict } from '@/entities/project/model/projectConflict';
import { errorMessage } from '@/shared/errors/errorMessage';
import { NativeAreaReview } from './NativeAreaReview';
import { SourceObjectReview } from './SourceObjectReview';
import { SourceSection, SourceSections } from './SourceSection';
import { SourceReadIssues } from './SourceReadIssues';
import { SourcePreparationError } from './SourcePreparationError';
import { LayerRecognitionStatus } from '@/entities/source-data/ui/LayerRecognitionStatus';
import { NativeFaceReview } from './NativeFaceReview';
import type { SourceLayerFormProps } from './SourceLayerForm.props';
export const SourceLayerForm: FC<SourceLayerFormProps> = ({
  sourceWarnings,
  incompleteConstraintLayers,
  unconfirmedMappings,
  partialAccepted,
  hasPlanningBoundary,
  reviewOnly,
  preparationBlocked,
  layers,
  mappings,
  setMappings,
  dataPassportQuery,
  mutationError,
  reloadAfterConflict,
  projectQuery,
  nativeAreaProposals,
  decideNativeArea,
  layerRecognition,
  allSavedLayersConfirmed,
  automaticAcceptancePending,
  layerRecognitionQuery,
  retryLayerRecognition,
  reviewRequest,
}) => {
  const hasBoundaryCandidates = layers.some(
    (layer) => layer.boundary_candidate?.status === 'usable',
  );
  const boundaryIssues = layers.filter(
    (layer) =>
      layer.boundary_candidate?.status === 'invalid' &&
      layer.boundary_candidate.issue,
  );
  return (
    <SourceSections
      reviewRequest={reviewRequest}
      defaultSection={
        !hasPlanningBoundary && hasBoundaryCandidates ? '01' :
          unconfirmedMappings.length ? '03' :
          !partialAccepted && incompleteConstraintLayers.length ? '02' : '01'
      }
    >
      <SourceSection
        number="01"
        title="Территория"
        description="Граница расчёта"
      >
        {!reviewOnly && (
          <BoundaryCandidatePicker
            readOnly={preparationBlocked}
            requireAttestation={projectQuery.data?.import_status?.mode === 'autocad_live'}
            layers={layers}
            mappings={mappings}
            onChange={setMappings}
          />
        )}
      </SourceSection>
      <SourceSection
        number="02"
        title="Геометрия"
        description="Контуры, замыкания и пропущенные объекты"
      >
        {!!sourceWarnings.length && (
          <Disclosure
            variant="panel"
            title={`Замечания к файлу (${sourceWarnings.length})`}
          >
            <ul className="m-0 list-disc space-y-1 pl-5 text-xs">
              {sourceWarnings.map((warning) => (
                <li className="wrap-anywhere" key={warning}>
                  {warning}
                </li>
              ))}
            </ul>
          </Disclosure>
        )}
        <NativeAreaReview
          projectId={projectQuery.data?.id ?? ''}
          sourceSha256={projectQuery.data?.source_file?.content_sha256 ?? ''}
          proposals={nativeAreaProposals}
          automatic={!!projectQuery.data?.source_file?.native_session}
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
                /* Keep the unconfirmed decision visible on read failure. */
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
        <div className="flex flex-wrap gap-2">
          {projectQuery.data && (
            <NativeFaceReview
              project={projectQuery.data}
              disabled={preparationBlocked}
            />
          )}
          {!reviewOnly && projectQuery.data && (
            <SourceObjectReview
              project={projectQuery.data}
              disabled={preparationBlocked}
            />
          )}
          {projectQuery.data?.source_file?.cad_snapshot_provenance
            ?.live_capture && (
            <SourceReadIssues
              projectId={projectQuery.data.id ?? ''}
              sourceSha={projectQuery.data.source_file.content_sha256 ?? ''}
            />
          )}
        </div>
        {!reviewOnly && !!incompleteConstraintLayers.length && (
          <InlineMessage tone="warning" title="Часть геометрии недоступна">
            <p className="m-0">
              {partialAccepted
                ? 'Карта строится по доступным объектам. Пропуски остаются в отчёте.'
                : 'Кнопка «Подготовить карту» сохранит решение считать по доступным объектам. Пропуски останутся в отчёте и не будут проверены.'}
            </p>
          </InlineMessage>
        )}
        {!reviewOnly && !!boundaryIssues.length && (
          <InlineMessage
            tone="warning"
            title="Граница территории требует проверки"
          >
            <p className="m-0">
              Контур не принят автоматически за всю территорию.
            </p>
            <ul className="mt-2 mb-0 list-disc pl-5">
              {boundaryIssues.map((layer) => (
                <li className="wrap-anywhere" key={layer.id}>
                  {layer.source_name}: {layer.boundary_candidate?.issue}
                </li>
              ))}
            </ul>
            <p className="mt-2 mb-0">
              Можно открыть карту и задать рабочую область вручную;
              подтверждённые ограничения сохранятся.
            </p>
          </InlineMessage>
        )}
        {!reviewOnly &&
          !hasPlanningBoundary &&
          !hasBoundaryCandidates &&
          !boundaryIssues.length && (
            <InlineMessage tone="info" title="Границу можно задать на карте">
              В источнике нет подтверждённой границы участка. После подготовки
              карты обведите рабочую область вручную; ограничения от
              подтверждённых слоёв останутся видны.
            </InlineMessage>
          )}
      </SourceSection>
      <SourceSection
        number="03"
        title="Слои"
        description="Проверьте, как слои влияют на посадки"
      >
        {!allSavedLayersConfirmed && (
          <LayerRecognitionStatus
            recognition={layerRecognition}
            loading={layerRecognitionQuery.isLoading}
            error={
              layerRecognitionQuery.isError || retryLayerRecognition?.isError
            }
            retrying={retryLayerRecognition?.isPending}
            onRetry={() => retryLayerRecognition.mutate()}
          />
        )}
        <LayerMappingWorkspace
          key={projectQuery.data?.id}
          recognition={layerRecognition}
          automaticAcceptancePending={automaticAcceptancePending}
          readOnly={reviewOnly || preparationBlocked}
          layers={layers}
          mappings={mappings}
          onChange={setMappings}
        />
      </SourceSection>
      {dataPassportQuery.data && (
        <SourceSection
          number="04"
          title="Сведения"
          description="Полнота исходных данных"
        >
          <DataPassportPanel passport={dataPassportQuery.data} header={null} />
        </SourceSection>
      )}
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
    </SourceSections>
  );
};
