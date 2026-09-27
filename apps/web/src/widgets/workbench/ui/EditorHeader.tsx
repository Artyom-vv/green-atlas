import { ControlProvider, Icon, IconButton, Text } from '@green/ui';
import { ArrowLeft, Map } from 'lucide-react';
import type { FC, ReactNode } from 'react';

export interface EditorHeaderProps {
  name: string;
  onBack: () => void;
  children: ReactNode;
}

export const EditorHeader: FC<EditorHeaderProps> = ({
  name,
  onBack,
  children,
}) => (
  <ControlProvider size="compact">
    <header className="flex min-h-14 shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-neutral-200 bg-white px-4 py-2">
      <div className="flex min-w-0 flex-1 basis-56 items-center gap-2">
        <IconButton
          icon={<ArrowLeft />}
          label="К списку проектов"
          variant="ghost"
          onClick={onBack}
        />
        <Icon icon={<Map />} />
        <Text as="h1" variant="heading" className="truncate" title={name}>
          {name}
        </Text>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {children}
      </div>
    </header>
  </ControlProvider>
);
