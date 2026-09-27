import { Button, Text, cx, type ButtonProps } from '@green/ui';
import type { FC, ReactNode } from 'react';

export interface AssistantRecordButtonProps extends Omit<
  ButtonProps,
  'title' | 'children' | 'content'
> {
  title: ReactNode;
  description: ReactNode;
}
export const AssistantRecordButton: FC<AssistantRecordButtonProps> = ({
  title,
  description,
  className,
  ...props
}) => (
  <Button
    variant="ghost"
    {...props}
    className={cx(
      'h-auto w-full justify-start py-2 text-left aria-[current]:bg-blue-100',
      className,
    )}
    content={
      <span className="grid min-w-0 gap-1">
        <Text variant="label" className="line-clamp-2 wrap-anywhere">
          {title}
        </Text>
        <Text variant="caption">{description}</Text>
      </span>
    }
  />
);
