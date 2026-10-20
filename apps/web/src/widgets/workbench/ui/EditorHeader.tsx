import { ControlProvider, Icon, IconButton, Text } from '@green/ui';
import { ArrowLeft, Map } from 'lucide-react';
import type { FC, ReactNode, Ref } from 'react';
import { ComponentSettings } from '@/features/component-settings/ComponentSettings';

export interface EditorHeaderProps {
  name: string;
  onBack: () => void;
  children: ReactNode;
  variant?: 'document' | 'editor';
  pending?: boolean;
  progressSlotRef?: Ref<HTMLDivElement>;
}

export const EditorHeader: FC<EditorHeaderProps> = ({
  name,
  onBack,
  children,
  variant = 'document',
  pending = false,
  progressSlotRef,
}) => (
  <ControlProvider size="compact">
    <header className="relative shrink-0 border-b border-neutral-200 bg-white">
      <div
        data-slot="header-main"
        className={`flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 p-2 ${variant === 'editor' ? 'min-h-12' : 'min-h-14 justify-between'}`}
      >
        <div
          role="group"
          aria-label="Проект и навигация"
          className="flex min-w-0 flex-1 basis-56 items-center gap-2"
        >
          <IconButton
            icon={<ArrowLeft />}
            label="К списку проектов"
            variant="ghost"
            onClick={onBack}
          />
          {variant === 'document' && <Icon icon={<Map />} />}
          <Text as="h1" variant="heading" className="truncate" title={name}>
            {name}
          </Text>
        </div>
        <div
          className={
            variant === 'editor'
              ? 'flex shrink-0 items-center gap-4'
              : 'flex shrink-0 flex-wrap items-center gap-2'
          }
        >
          {children}
          <ComponentSettings />
        </div>
      </div>
      <div
        ref={progressSlotRef}
        data-slot="header-pending"
        data-pending={pending || undefined}
        aria-hidden="true"
        className="group/header-progress pointer-events-none absolute inset-x-0 bottom-0 h-0.5 overflow-hidden"
      >
        <span className="absolute inset-y-0 left-0 hidden w-[35%] bg-(--primary) group-has-[[data-slot=scene-pending]]/header-progress:block group-data-[pending]/header-progress:block motion-safe:animate-[progress-indeterminate_1.8s_ease-in-out_infinite] motion-reduce:left-1/3" />
      </div>
    </header>
  </ControlProvider>
);
