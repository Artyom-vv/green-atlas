/** Раскрываемый раздел интерфейса: заголовок, действия и сохраняемое содержимое. */
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
  /** The embedded content owns its gutter instead of adding section padding. */
  contentVariant?: 'default' | 'flush';
  /** Удерживает заголовок раздела у верхней границы родительской области прокрутки. */
  stickyHeader?: boolean;
  /** Compact inset controls, optionally with actions after the chevron. */
  headerVariant?: 'default' | 'inset' | 'trailing-actions';
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
  contentVariant = 'default',
  stickyHeader = false,
  headerVariant = 'default',
}) => {
  const [localExpanded, setExpanded] = useState(defaultOpen);
  const expanded = open ?? localExpanded;
  const contentId = useId();
  const size = useControlSize(controlSize);
  const hasActions = actions != null;
  const styles = disclosure({
    variant,
    contentVariant,
    stickyHeader,
    hasActions,
    headerVariant,
  });
  return (
    <section
      className={styles.root({ className })}
      aria-label={label}
      data-expanded={expanded || undefined}
    >
      <div className={styles.header()}>
        <button
          type="button"
          className={styles.trigger({
            className: cx(variant !== 'section' && controlSizes[size]),
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
                <span className="mt-1 block text-xs leading-4 font-normal text-neutral-600">
                  {description}
                </span>
              )}
            </span>
          </span>
          <Icon
            icon={<ChevronDown />}
            className={cx(
              'transition-transform',
              hasActions &&
                (headerVariant === 'trailing-actions'
                  ? 'col-start-2'
                  : 'col-start-3'),
              expanded && 'rotate-180',
            )}
          />
        </button>
        {hasActions && (
          <div
            className={cx(
              'relative z-1 row-start-1 flex shrink-0 items-center gap-1',
              headerVariant === 'trailing-actions'
                ? 'col-start-3'
                : 'col-start-2',
            )}
          >
            {actions}
          </div>
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
