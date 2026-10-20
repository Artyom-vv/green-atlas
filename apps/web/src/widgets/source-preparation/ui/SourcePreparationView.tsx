import { useState, type FC } from 'react';
import { Button, ControlProvider } from '@green/ui';
import { ArrowLeft } from 'lucide-react';
import { AppHeader } from '@/shared/ui/AppHeader';
import { ProjectLocationButton } from '@/features/project-location';
import { PreparationStatus } from './PreparationStatus';
import { PreparationActions } from './PreparationActions';
import { SourceLayerForm } from './SourceLayerForm';
import { SourceRecognitionIndicator } from './SourceRecognitionIndicator';
import { OperationProgress } from '@/entities/operation/ui/OperationProgress';
import { operationActive } from '@/entities/operation/model/operationPresentation';
import { PREPARATION_MESSAGES } from '@/entities/source-data/config/sourceMessages';
import { PreparationStatusPropsFor } from './PreparationStatus.props';
import { PreparationActionsPropsFor } from './PreparationActions.props';
import { SourceLayerFormPropsFor } from './SourceLayerForm.props';
import type { SourcePreparationViewProps } from './SourcePreparationView.props';

export const SourcePreparationView: FC<SourcePreparationViewProps> = (
  props,
) => {
  const [reviewRequest, setReviewRequest] = useState<{
    section: string;
    sequence: number;
  }>();
  const requestReview = (section: string) =>
    setReviewRequest((previous) => ({
      section,
      sequence: (previous?.sequence ?? 0) + 1,
    }));
  return (
    <ControlProvider size="default">
      <div className="flex h-dvh min-h-0 min-w-0 flex-col overflow-hidden">
        <AppHeader
          projectName={props.projectName}
          variant="preparation"
          leading={
            props.mapReady ? (
              <Button
                variant="ghost"
                startIcon={<ArrowLeft />}
                onClick={props.onPlan}
              >
                К плану
              </Button>
            ) : undefined
          }
          endActions={
            <>
              {props.projectQuery.data?.id && (
                <ProjectLocationButton projectId={props.projectQuery.data.id} />
              )}
              <SourceRecognitionIndicator
                {...props}
                onReview={() => requestReview('03')}
              />
            </>
          }
        />
        <main className="flex min-h-0 flex-1">
          <SourceLayerForm
            {...SourceLayerFormPropsFor(props)}
            initialSection={props.initialSection}
            reviewRequest={reviewRequest}
            intro={props.reviewOnly ? props.sourceReviewMessage : undefined}
            status={<PreparationStatus {...PreparationStatusPropsFor(props)} />}
            footer={
              props.operation &&
              operationActive(props.operation) &&
              !props.statusUnknown ? (
                <OperationProgress
                  presentation="footer"
                  operation={props.operation}
                  title={
                    props.operation.kind === 'prepare_cad_capture'
                      ? PREPARATION_MESSAGES.preparingDrawing
                      : PREPARATION_MESSAGES.preparingMap
                  }
                  stageLabel={
                    props.previousSourceOperation
                      ? PREPARATION_MESSAGES.previousDrawing
                      : undefined
                  }
                  actionBusy={
                    props.cancelOperation.isPending ||
                    props.saveMutation.isPending
                  }
                  onCancel={() =>
                    props.cancelOperation.mutate(props.operation!.id!)
                  }
                />
              ) : (
                <PreparationActions
                  {...PreparationActionsPropsFor(props)}
                  status={
                    props.operation && !operationActive(props.operation) ? (
                      <OperationProgress
                        presentation="footer"
                        operation={props.operation}
                        title={
                          props.operation.kind === 'prepare_cad_capture'
                            ? PREPARATION_MESSAGES.preparingDrawing
                            : PREPARATION_MESSAGES.preparingMap
                        }
                        actionBusy={props.saveMutation.isPending}
                        onDownloadSource={() => {
                          window.location.href = props.downloadSource();
                        }}
                      />
                    ) : undefined
                  }
                  onImport={props.onImport}
                  onPlan={props.onPlan}
                  mapReady={props.mapReady}
                  onNeedsReview={requestReview}
                />
              )
            }
          />
        </main>
      </div>
    </ControlProvider>
  );
};
