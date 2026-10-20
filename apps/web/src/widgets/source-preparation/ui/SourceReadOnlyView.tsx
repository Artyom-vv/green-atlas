import { useState, type FC } from 'react';
import { Button, ControlProvider } from '@green/ui';
import { ArrowLeft } from 'lucide-react';
import { AppHeader } from '@/shared/ui/AppHeader';
import { ProjectLocationButton } from '@/features/project-location';
import { SourceReadOnlyDocument } from './SourceReadOnlyDocument';
import { SourceRecognitionIndicator } from './SourceRecognitionIndicator';
import {
  SourceReadOnlyDocumentPropsFor,
  type SourceReadOnlyDocumentProps,
} from './SourceReadOnlyDocument.props';

export interface SourceReadOnlyViewProps extends SourceReadOnlyDocumentProps {
  projectName: string;
  onBack: () => void;
  initialSection?: '02' | '03' | '04';
}

export const SourceReadOnlyView: FC<SourceReadOnlyViewProps> = (props) => {
  const [activeSection, setActiveSection] = useState(
    props.initialSection ?? '04',
  );
  return (
    <ControlProvider size="default">
      <div className="flex h-dvh min-h-0 min-w-0 flex-col overflow-hidden">
        <AppHeader
          projectName={props.projectName}
          variant="preparation"
          leading={
            <Button
              variant="ghost"
              startIcon={<ArrowLeft />}
              onClick={props.onPlan}
            >
              К плану
            </Button>
          }
          endActions={
            <>
              {props.projectQuery.data?.id && (
                <ProjectLocationButton projectId={props.projectQuery.data.id} />
              )}
              <SourceRecognitionIndicator
                {...props}
                onReview={() => setActiveSection('03')}
              />
            </>
          }
        />
        <main className="flex min-h-0 min-w-0 flex-1">
          <SourceReadOnlyDocument
            {...SourceReadOnlyDocumentPropsFor(props)}
            startSourceEditing={props.startSourceEditing}
            onPlan={props.onPlan}
            activeSection={activeSection}
            onSectionChange={setActiveSection}
          />
        </main>
      </div>
    </ControlProvider>
  );
};
