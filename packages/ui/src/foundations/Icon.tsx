import type { LucideIcon } from 'lucide-react';
import {
  cloneElement,
  createElement,
  isValidElement,
  type ComponentType,
  type FC,
  type ReactElement,
  type ReactNode,
  type SVGProps,
} from 'react';
import { cx } from './utils';

/** Constructor support is retained for existing Lucide consumers. */
export type IconNode = ReactNode | LucideIcon;

export interface IconProps {
  icon: IconNode;
  size?: number;
  label?: string;
  className?: string;
}

export const Icon: FC<IconProps> = ({ icon, size = 16, label, className }) => {
  const normalizeElement = (element: ReactElement<SVGProps<SVGSVGElement>>) =>
    cloneElement(element, {
      width: size,
      height: size,
      color: 'currentColor',
      ...(element.props.stroke && element.props.stroke !== 'none'
        ? { stroke: 'currentColor' }
        : {}),
      ...(element.props.fill && element.props.fill !== 'none'
        ? { fill: 'currentColor' }
        : {}),
      style: {
        ...element.props.style,
        width: size,
        height: size,
        color: 'inherit',
      },
      'aria-hidden': true,
    });
  // A React element and a forwardRef constructor both expose $$typeof.
  // Elements must be handled before the legacy constructor adapter.
  const content = isValidElement<SVGProps<SVGSVGElement>>(icon)
    ? normalizeElement(icon)
    : typeof icon !== 'object' && typeof icon !== 'function'
      ? icon
      : icon &&
          (typeof icon === 'function' ||
            ('$$typeof' in icon &&
              icon.$$typeof === Symbol.for('react.forward_ref')))
        ? normalizeElement(
            createElement(icon as ComponentType<SVGProps<SVGSVGElement>>, {}),
          )
        : icon;

  if (content === null || content === undefined || content === false)
    return null;

  return (
    <span
      data-slot="icon"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={cx(
        'inline-flex shrink-0 items-center justify-center leading-none [&>svg]:size-full [&>svg]:shrink-0 [&>svg]:text-current',
        className,
      )}
      style={{ width: size, height: size }}
    >
      {content}
    </span>
  );
};
