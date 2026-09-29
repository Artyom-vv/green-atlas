import { useState, type FC } from 'react';
import { AppHeader } from '@/shared/ui/AppHeader';
import { FlowDocument, ProjectSteps } from '@/widgets/project-flow';
import { PreparationStatus } from './PreparationStatus';
import { PreparationActions } from './PreparationActions';
import { SourceLayerForm } from './SourceLayerForm';
import { PreparationStatusPropsFor } from './PreparationStatus.props';
import { PreparationActionsPropsFor } from './PreparationActions.props';
import { SourceLayerFormPropsFor } from './SourceLayerForm.props';
import type { SourcePreparationViewProps } from './SourcePreparationView.props';
export const SourcePreparationView: FC<SourcePreparationViewProps> = (
  props,
) => {
  const [reviewRequest, setReviewRequest] = useState<{ section: string; sequence: number }>();
  const {
    projectName,
    reviewOnly,
    onImport,
    onPlan,
    mapReady,
    cadPreview,
    sourceReviewMessage,
  } = props;
  return (
    <div className="flex h-dvh min-h-0 min-w-0 flex-col overflow-hidden">
      <AppHeader projectName={projectName} />
      <main
        className={
          reviewOnly
            ? 'mx-auto flex min-h-0 w-full max-w-280 flex-1'
            : 'grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] min-[1100px]:grid-cols-[200px_minmax(0,1fr)] min-[1200px]:grid-cols-[240px_minmax(0,1fr)]'
        }
      >
        {!reviewOnly && (
          <div className="hidden min-h-0 min-[1100px]:contents">
            <ProjectSteps active={2} projectName={projectName} />
          </div>
        )}
        <FlowDocument
          layout="panels"
          title={
            cadPreview
              ? 'Предварительная карта'
              : reviewOnly
                ? 'План только для просмотра'
                : 'Проверьте слои'
          }
          description={
            reviewOnly
              ? (sourceReviewMessage ??
                'В отдельном DXF нет всех данных для продолжения проекта. Они сохраняются в полном ZIP-пакете выпуска.')
              : 'Проверьте территорию и назначения слоёв'
          }
          footer={
            <PreparationActions
              {...PreparationActionsPropsFor(props)}
              onImport={onImport}
              onPlan={onPlan}
              mapReady={mapReady}
              onNeedsReview={(section) => setReviewRequest((previous) => ({
                section, sequence: (previous?.sequence ?? 0) + 1,
              }))}
            />
          }
        >
          <div className="flex flex-col">
            <div className="px-4 empty:hidden sm:px-6">
              <PreparationStatus {...PreparationStatusPropsFor(props)} />
            </div>
            <SourceLayerForm {...SourceLayerFormPropsFor(props)} reviewRequest={reviewRequest} />
          </div>
        </FlowDocument>
      </main>
    </div>
  );
};
