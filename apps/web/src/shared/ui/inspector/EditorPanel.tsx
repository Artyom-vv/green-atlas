import type { AriaAttributes, FC, ReactNode } from 'react';
import {
  ControlProvider,
  Disclosure,
  Field,
  FormActions,
  IconButton,
  NumberInput,
  Panel,
  ScrollArea,
} from '@green/ui';
import { PanelRightClose } from 'lucide-react';

export interface EditorPanelProps {
  title: ReactNode;
  children: ReactNode;
  onClose?: () => void;
  label?: string;
  headerActions?: ReactNode;
  header?: ReactNode;
}

export const EditorPanel: FC<EditorPanelProps> = ({
  title,
  children,
  onClose,
  label,
  headerActions,
  header,
}) => (
  <ControlProvider size="compact">
    <Panel
      className="@container/inspector flex min-h-0 flex-1 flex-col text-xs text-neutral-800"
      aria-label={label}
      title={title}
      header={header}
      body={
        <ScrollArea
          className="flex-1"
          contentClassName="grid content-start gap-4 p-4"
        >
          {children}
        </ScrollArea>
      }
      actions={
        <>
          {headerActions}
          {onClose ? (
            <IconButton
              icon={<PanelRightClose />}
              variant="ghost"
              label="Скрыть боковую панель"
              onClick={onClose}
            />
          ) : null}
        </>
      }
    >
      {children}
    </Panel>
  </ControlProvider>
);

export const EditorField = Field;

export interface EditorNumberProps extends Pick<
  AriaAttributes,
  'aria-describedby' | 'aria-invalid'
> {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step?: number;
  disabled?: boolean;
}

export const EditorNumber: FC<EditorNumberProps> = ({
  label,
  onChange,
  ...props
}) => (
  <NumberInput
    controlSize="compact"
    aria-label={label}
    onValueChange={onChange}
    {...props}
  />
);

export interface EditorDisclosureProps {
  title: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}

export const EditorDisclosure: FC<EditorDisclosureProps> = (props) => (
  <Disclosure
    variant="plain"
    controlSize="compact"
    contentClassName="py-2"
    {...props}
  />
);

export interface EditorActionsProps {
  children: ReactNode;
  grid?: boolean;
}

export const EditorActions: FC<EditorActionsProps> = ({ children, grid }) => (
  <FormActions
    layout={grid ? 'equal' : 'inline'}
    className="mt-3 justify-start"
  >
    {children}
  </FormActions>
);
