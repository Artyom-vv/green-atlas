import { ChevronDown, CircleHelp } from 'lucide-react';
import { useId, useState, type FC, type ReactNode } from 'react';
import type { VariantProps } from 'tailwind-variants';
import { useControlSize } from '../foundations/ControlProvider';
import { Icon } from '../foundations/Icon';
import { controlSizes, cx, type ControlSize } from '../foundations/utils';

export interface DisclosureProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  label?: string;
  children: ReactNode;
  defaultOpen?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  variant?: NonNullable<VariantProps<typeof disclosure>['variant']>;
  controlSize?: ControlSize;
  startIcon?: ReactNode;
  className?: string;
  contentClassName?: string;
}

export const Disclosure: FC<DisclosureProps> = ({
  title,
  description,
  actions,
  label,
  children,
  defaultOpen = false,
  open,
  onOpenChange,
  variant = 'framed',
  controlSize,
  startIcon,
  className,
  contentClassName,
}) => {
  const [localExpanded, setExpanded] = useState(defaultOpen);
  const expanded = open ?? localExpanded;
  const contentId = useId();
  const size = useControlSize(controlSize);
  const styles = disclosure({ variant });
  return (
    <section
      className={styles.root({ className })}
      aria-label={label}
      data-expanded={expanded || undefined}
    >
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          className={styles.trigger({
            className: cx(
              controlSizes[size],
              actions != null && 'w-auto min-w-0 flex-1',
            ),
          })}
          data-size={size}
          aria-expanded={expanded}
          aria-controls={contentId}
          onClick={() => {
            if (open === undefined) setExpanded(!expanded);
            onOpenChange?.(!expanded);
          }}
        >
          <span className="inline-flex min-w-0 flex-1 items-center gap-3">
            <Icon icon={startIcon} />
            <span className="min-w-0 flex-1 wrap-anywhere">
              <span className="block">{title}</span>
              {description != null && (
                <span className="mt-1 block text-xs leading-5 font-normal text-neutral-600">
                  {description}
                </span>
              )}
            </span>
          </span>
          <Icon
            icon={<ChevronDown />}
            className={cx(
              'text-neutral-500 transition-transform',
              expanded && 'rotate-180',
            )}
          />
        </button>
        {actions != null && (
          <div className="flex shrink-0 items-center gap-1">{actions}</div>
        )}
      </div>
      <div
        id={contentId}
        hidden={!expanded}
        inert={!expanded}
        className={styles.content({ className: contentClassName })}
      >
        {children}
      </div>
    </section>
  );
};

export const HelpDisclosure: FC<DisclosureProps> = (props) => (
  <Disclosure
    {...props}
    variant={props.variant ?? 'plain'}
    startIcon={props.startIcon ?? <CircleHelp />}
  />
);

import { disclosure } from './disclosureVariants';
