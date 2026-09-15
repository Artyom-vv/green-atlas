import type { CSSProperties, FC } from 'react';
import { featureAvailability } from '@/shared/config/featureAvailability';
import { Button, ResizeHandle } from '@green/ui';
import { Map as MapIcon } from 'lucide-react';
import { EditorHeader } from '@/widgets/workbench/ui/EditorHeader';
import {
  ProjectAssistantTrigger,
  ProjectAssistantSidebar,
  type useProjectAssistant,
} from '@/features/assistant';
import { SourceReadOnlyDocument } from './SourceReadOnlyDocument';
import {
  SourceReadOnlyDocumentPropsFor,
  type SourceReadOnlyDocumentProps,
} from './SourceReadOnlyDocument.props';
export interface SourceReadOnlyViewProps extends SourceReadOnlyDocumentProps {
  projectName: string;
  onBack: () => void;
  assistant: ReturnType<typeof useProjectAssistant>;
}
export const SourceReadOnlyView: FC<SourceReadOnlyViewProps> = (props) => {
  const { projectName, onBack, onPlan, assistant } = props;
  const assistantOpen = featureAvailability.assistant && assistant.open;
  return (
    <div
      className="flex h-dvh min-h-0 flex-col overflow-hidden"
      style={{ '--assistant-width': `${assistant.width}px` } as CSSProperties}
    >
      <EditorHeader name={projectName} onBack={onBack}>
        <ProjectAssistantTrigger />
        <Button variant="secondary" icon={MapIcon} onClick={onPlan}>
          К плану
        </Button>
      </EditorHeader>
      <div
        data-assistant-open={assistantOpen}
        className="relative grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] grid-rows-[minmax(0,1fr)] overflow-clip min-[1024px]:data-[assistant-open=true]:grid-cols-[var(--assistant-width)_minmax(0,1fr)]"
      >
        {featureAvailability.assistant && (
          <div
            hidden={!assistantOpen}
            className="relative col-start-1 row-start-1 min-h-0 min-w-0 border-r border-neutral-200 bg-white max-[1023px]:absolute max-[1023px]:inset-y-0 max-[1023px]:left-0 max-[1023px]:z-50 max-[1023px]:w-[min(var(--assistant-width),calc(100vw-48px))]"
          >
            <ProjectAssistantSidebar docked />
            <ResizeHandle
              className="absolute inset-y-0 -right-1 z-10"
              label="Ширина чата"
              orientation="vertical"
              value={assistant.width}
              min={assistant.minWidth}
              max={assistant.maxWidth}
              onChange={assistant.setWidth}
              onReset={assistant.resetWidth}
            />
          </div>
        )}
        <main className="mx-auto flex min-h-0 w-full max-w-280 min-w-0 overflow-clip">
          <SourceReadOnlyDocument
            {...SourceReadOnlyDocumentPropsFor(props)}
            onPlan={onPlan}
          />
        </main>
      </div>
    </div>
  );
};
