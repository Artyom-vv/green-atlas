import type { FC } from 'react';
import { Button, Disclosure, InlineMessage } from '@green/ui';
import { LayerMappingTable } from '@/entities/source-data/ui/LayerMappingTable';
import { LayerMappingReview } from '@/entities/source-data/ui/LayerMappingReview';
import { BoundaryCandidatePicker } from '@/entities/source-data/ui/BoundaryCandidatePicker';
import { DataPassportPanel } from '@/entities/source-data/ui/DataPassportPanel';
import { ProjectConflictNotice } from '@/entities/project/ui/ProjectConflictNotice';
import { isProjectConflict } from '@/entities/project/model/projectConflict';
import { errorMessage } from '@/shared/errors/errorMessage';
import { NativeAreaReview } from './NativeAreaReview';
import { SourceObjectReview } from './SourceObjectReview';
import { SourceSection } from './SourceSection';
import { SourceReadIssues } from './SourceReadIssues';
import { NativeFaceReview } from './NativeFaceReview';
import type { SourceLayerFormProps } from './SourceLayerForm.props';
export const SourceLayerForm: FC<SourceLayerFormProps> = ({
  sourceWarnings,
  unconfirmedMappings,
  incompleteConstraintLayers,
  partialAccepted,
  acceptPartialGeometry,
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
  layerRecognitionQuery,
}) => {
  const hasBoundaryCandidates = layers.some(
    (layer) => layer.boundary_candidate?.status === 'usable',
  );
  const boundaryIssues = layers.filter(
    (layer) =>
      layer.boundary_candidate?.status === 'invalid' &&
      layer.boundary_candidate.issue,
  );
  const unconfirmedIds = new Set(unconfirmedMappings.map((layer) => layer.id));
  const otherLayers = layers.filter((layer) => !unconfirmedIds.has(layer.id));
  return (
    <div className="space-y-6">
      <SourceSection number="01" title="Территория">
        {!reviewOnly && (
          <BoundaryCandidatePicker
            readOnly={preparationBlocked}
            layers={layers}
            mappings={mappings}
            onChange={setMappings}
          />
        )}
      </SourceSection>
      <SourceSection number="02" title="Геометрия">
        {!!sourceWarnings.length && (
          <Disclosure
            variant="plain"
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
        {layerRecognitionQuery.isError && (
          <InlineMessage tone="warning">
            Предложения типов недоступны
            <Button
              variant="ghost"
              onClick={() => void layerRecognitionQuery.refetch()}
            >
              Повторить
            </Button>
          </InlineMessage>
        )}
        {!reviewOnly && !!incompleteConstraintLayers.length && (
          <InlineMessage tone="warning" title="Часть геометрии недоступна">
            <div className="space-y-2">
              <p className="m-0">
                {partialAccepted
                  ? 'Расчёт по доступным объектам разрешён — пропущенные объекты не проверяются'
                  : 'Для части объектов нет расчётной геометрии — они не будут проверяться'}
              </p>
              {!partialAccepted && (
                <Button
                  variant="secondary"
                  disabled={
                    preparationBlocked ||
                    !projectQuery.data?.source_file?.content_sha256
                  }
                  onClick={() => acceptPartialGeometry.mutate()}
                >
                  {acceptPartialGeometry.isPending
                    ? 'Сохраняем решение…'
                    : 'Использовать доступную геометрию'}
                </Button>
              )}
            </div>
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
      <SourceSection number="03" title="Слои">
        {!reviewOnly && !!unconfirmedMappings.length && (
          <LayerMappingReview
            recognition={layerRecognition}
            readOnly={preparationBlocked}
            layers={unconfirmedMappings}
            mappings={mappings}
            onChange={setMappings}
          />
        )}
        {!reviewOnly && !!unconfirmedMappings.length && !!otherLayers.length ? (
          <Disclosure
            variant="plain"
            title={`Остальные слои (${otherLayers.length})`}
          >
            <LayerMappingTable
              recognition={layerRecognition}
              readOnly={preparationBlocked}
              layers={otherLayers}
              mappings={mappings}
              onChange={setMappings}
            />
          </Disclosure>
        ) : !unconfirmedMappings.length &&
          hasBoundaryCandidates &&
          !reviewOnly ? (
          <Disclosure variant="plain" title={`Другие слои (${layers.length})`}>
            <LayerMappingTable
              recognition={layerRecognition}
              readOnly={preparationBlocked}
              layers={layers}
              mappings={mappings}
              onChange={setMappings}
            />
          </Disclosure>
        ) : !unconfirmedMappings.length || reviewOnly ? (
          <LayerMappingTable
            recognition={layerRecognition}
            readOnly={reviewOnly || preparationBlocked}
            layers={layers}
            mappings={mappings}
            onChange={setMappings}
          />
        ) : null}
      </SourceSection>
      {dataPassportQuery.data && (
        <SourceSection number="04" title="Сведения">
          <Disclosure variant="plain" title="Полнота исходных данных">
            <DataPassportPanel
              passport={dataPassportQuery.data}
              header={null}
            />
          </Disclosure>
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
          <InlineMessage tone="error">
            Не удалось подготовить карту. Проверьте слои и повторите попытку.
          </InlineMessage>
        ))}
    </div>
  );
};
