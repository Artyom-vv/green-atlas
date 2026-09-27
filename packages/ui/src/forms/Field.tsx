import { useCallback, useId, useState, type FC, type ReactNode } from 'react';
import { cx } from '../foundations/utils';
import { FieldContext } from './fieldContext';

export interface FieldProps {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  disabled?: boolean;
  htmlFor?: string;
  children: ReactNode;
  className?: string;
}

export const Field: FC<FieldProps> = ({
  label,
  hint,
  error,
  required,
  disabled,
  htmlFor,
  children,
  className,
}) => {
  const id = useId();
  const [registeredId, setRegisteredId] = useState<string>();
  const controlId = htmlFor ?? registeredId ?? `${id}-control`;
  const labelId = `${id}-label`;
  const descriptionId = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  const registerControl = useCallback((nextId: string) => {
    setRegisteredId(nextId);
    return () =>
      setRegisteredId((current) => (current === nextId ? undefined : current));
  }, []);
  return (
    <FieldContext
      value={{
        controlId,
        labelId,
        descriptionId,
        invalid: Boolean(error),
        required,
        disabled,
        registerControl,
      }}
    >
      <div
        data-slot="field"
        className={cx('flex min-w-0 flex-col gap-1.5 text-sm', className)}
      >
        <label
          id={labelId}
          htmlFor={controlId}
          className="text-xs leading-4 text-neutral-600"
        >
          {label}
          {!!required && <span aria-hidden="true"> *</span>}
        </label>
        {children}
        {error ? (
          <div
            id={descriptionId}
            className="text-error text-xs leading-4"
            role="alert"
          >
            {error}
          </div>
        ) : hint ? (
          <div
            id={descriptionId}
            className="text-xs leading-4 text-neutral-500"
          >
            {hint}
          </div>
        ) : null}
      </div>
    </FieldContext>
  );
};

export const FormField = Field;
