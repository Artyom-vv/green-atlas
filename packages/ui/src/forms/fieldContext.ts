import {
  createContext,
  useContext,
  useEffect,
  type InputHTMLAttributes,
} from 'react';

export interface FieldMetadata {
  controlId: string;
  labelId: string;
  descriptionId?: string;
  invalid: boolean;
  required?: boolean;
  disabled?: boolean;
  registerControl: (id: string) => () => void;
}
export const FieldContext = createContext<FieldMetadata | undefined>(undefined);

export interface FieldControlMetadata extends Pick<
  InputHTMLAttributes<HTMLInputElement>,
  | 'id'
  | 'aria-label'
  | 'aria-labelledby'
  | 'aria-describedby'
  | 'aria-invalid'
  | 'required'
  | 'disabled'
> {}

const joinIds = (...values: Array<string | undefined>) => {
  const ids = values.flatMap(
    (value) => value?.split(/\s+/).filter(Boolean) ?? [],
  );
  return ids.length ? [...new Set(ids)].join(' ') : undefined;
};

/** Adds field semantics only; value, events and ref remain caller-owned. */
export function useFieldControl<T extends FieldControlMetadata>(props: T): T {
  const field = useContext(FieldContext);
  const explicitId = props.id;
  const registerControl = field?.registerControl;
  useEffect(() => {
    if (explicitId && registerControl) return registerControl(explicitId);
  }, [explicitId, registerControl]);
  if (!field) return props;
  return {
    ...props,
    id: props.id ?? field.controlId,
    'aria-labelledby':
      props['aria-labelledby'] ??
      (props['aria-label'] ? undefined : field.labelId),
    'aria-describedby': joinIds(props['aria-describedby'], field.descriptionId),
    'aria-invalid': field.invalid ? true : props['aria-invalid'],
    required: props.required || field.required,
    disabled: props.disabled || field.disabled,
  };
}
