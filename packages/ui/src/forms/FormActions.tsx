import {
  type ComponentPropsWithRef,
  type CSSProperties,
  type FC,
  type ReactNode,
} from 'react';
import { tv, type VariantProps } from 'tailwind-variants';

const formActions = tv({
  base: 'min-w-0 items-center gap-2',
  variants: {
    layout: {
      inline: 'flex flex-wrap justify-end',
      equal:
        'grid grid-cols-[repeat(auto-fit,minmax(min(100%,var(--action-min-width)),1fr))] [&>*]:min-w-max',
    },
  },
  defaultVariants: { layout: 'inline' },
});

export interface FormActionsProps
  extends ComponentPropsWithRef<'div'>, VariantProps<typeof formActions> {
  children: ReactNode;
  /** Reserve enough width for the longest action; use 100% for a single column. */
  minItemWidth?: string;
}

interface FormActionsStyle extends CSSProperties {
  '--action-min-width': string;
}

export const FormActions: FC<FormActionsProps> = ({
  children,
  layout = 'inline',
  minItemWidth = '8rem',
  className,
  style,
  ...props
}) => {
  const actionStyle: FormActionsStyle = {
    '--action-min-width': minItemWidth,
    ...style,
  };
  return (
    <div
      className={formActions({ layout, className })}
      data-layout={layout}
      style={actionStyle}
      {...props}
    >
      {children}
    </div>
  );
};
