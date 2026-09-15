import type { CadDirectory } from '@green/api-client';
import { Button, ScrollArea, Text } from '@green/ui';
import { File, Folder } from 'lucide-react';
import { formatFileSize } from '@/shared/format/display';

interface Props {
  directory: CadDirectory;
  selected?: string;
  disabled: boolean;
  onNavigate: (path: string) => void;
  onSelect: (path: string) => void;
}

export function CadDirectoryList({
  directory,
  selected,
  disabled,
  onNavigate,
  onSelect,
}: Props) {
  return (
    <div className="overflow-hidden rounded border border-neutral-200">
      <div className="flex h-10 items-center border-b border-neutral-200 bg-neutral-100 px-3 text-xs">
        Чертежи и папки
      </div>
      <ScrollArea
        className="h-64"
        contentClassName="divide-y divide-neutral-200"
      >
        {directory.entries.map((entry) => (
          <Button
            key={entry.path}
            variant="ghost"
            className="h-auto min-h-10 w-full rounded-none px-3 py-2 aria-pressed:bg-blue-100 aria-pressed:text-blue-700"
            disabled={disabled}
            aria-pressed={
              entry.kind === 'drawing' ? selected === entry.path : undefined
            }
            startIcon={entry.kind === 'directory' ? <Folder /> : <File />}
            onClick={() =>
              entry.kind === 'directory'
                ? onNavigate(entry.path)
                : onSelect(entry.path)
            }
            content={
              <span className="flex min-w-0 flex-1 items-center gap-3 text-left">
                <span className="min-w-0 flex-1 truncate" title={entry.name}>
                  {entry.name}
                </span>
                {entry.bytes != null && (
                  <span className="w-20 shrink-0 text-right text-xs text-neutral-500 tabular-nums">
                    {formatFileSize(entry.bytes)}
                  </span>
                )}
              </span>
            }
          />
        ))}
        {!directory.entries.length && (
          <Text as="p" className="p-3">
            В этой папке нет чертежей.
          </Text>
        )}
      </ScrollArea>
    </div>
  );
}
