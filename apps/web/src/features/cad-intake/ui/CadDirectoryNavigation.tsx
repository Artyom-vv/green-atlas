import type { CadRoot } from '@green/api-client';
import { Button, Field, Select, Text } from '@green/ui';
import { ArrowUp } from 'lucide-react';

interface Props {
  roots: CadRoot[];
  rootId: string;
  path: string;
  disabled: boolean;
  onNavigate: (path: string, rootId?: string) => void;
}

export function CadDirectoryNavigation({
  roots,
  rootId,
  path,
  disabled,
  onNavigate,
}: Props) {
  const parent = path.split('/').slice(0, -1).join('/') || '.';
  return (
    <>
      <Field label="Комплект">
        <Select
          value={rootId}
          disabled={disabled}
          onChange={(event) => onNavigate('.', event.target.value)}
        >
          {roots.map((root) => (
            <option key={root.id} value={root.id}>
              {root.label}
            </option>
          ))}
        </Select>
      </Field>
      <div className="flex min-w-0 items-center gap-3">
        <Button
          variant="ghost"
          startIcon={<ArrowUp />}
          disabled={disabled || path === '.'}
          onClick={() => onNavigate(parent)}
        >
          Выше
        </Button>
        <Text variant="caption" className="min-w-0 truncate" title={path}>
          {path === '.' ? 'Корень комплекта' : path}
        </Text>
      </div>
    </>
  );
}
