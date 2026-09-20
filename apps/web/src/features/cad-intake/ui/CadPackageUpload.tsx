import { useId, useState, type ComponentProps } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api, type CadUploadPackage } from '@green/api-client';
import {
  Button,
  FileDropzone,
  InlineMessage,
  Progress,
  Radio,
} from '@green/ui';
import type { IntakeSelection } from '../api/startIntake';

const MAX_FILES = 128;
const MAX_FILE_BYTES = 768 * 1024 * 1024;
const MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024;
const ACCEPT = {
  'application/octet-stream': ['.dxf'],
  'application/json': ['.json'],
};

type DropzoneProps = ComponentProps<typeof FileDropzone>;

function selection(
  package_: CadUploadPackage,
  primaryPath: string,
): IntakeSelection {
  const primary = package_.entries.find((entry) => entry.path === primaryPath);
  if (!primary) throw new Error('Выберите основной DXF.');
  return {
    rootId: package_.root_id,
    path: primary.path,
    sha256: primary.sha256,
    additionalEntries: package_.entries
      .filter((entry) => entry.path !== primary.path)
      .map((entry) => ({ path: entry.path, sha256: entry.sha256 })),
  };
}

export function CadPackageUpload({
  busy,
  onStart,
}: {
  busy: boolean;
  onStart: (selection: IntakeSelection) => void;
}) {
  const [package_, setPackage] = useState<CadUploadPackage>();
  const [primary, setPrimary] = useState('');
  const [clientError, setClientError] = useState<string>();
  const groupName = useId();
  const upload = useMutation({
    mutationFn: (files: File[]) => api.uploadCadPackage(files),
    onSuccess: (value) => {
      setPackage(value);
      setPrimary(value.entries[0]?.path ?? '');
      setClientError(undefined);
    },
  });
  const acceptFiles: DropzoneProps['onFilesAccepted'] = (files) => {
    if (busy || upload.isPending) return;
    if (files.reduce((sum, file) => sum + file.size, 0) > MAX_TOTAL_BYTES) {
      setClientError('Комплект должен быть не больше 2 ГБ.');
      return;
    }
    setClientError(undefined);
    upload.mutate(files);
  };
  const reset = () => {
    upload.reset();
    setPackage(undefined);
    setPrimary('');
    setClientError(undefined);
  };

  if (package_) {
    return (
      <div className="grid gap-3">
        <div
          className="rounded-control grid max-h-48 gap-1 overflow-auto overscroll-contain border border-neutral-200 p-2"
          role="radiogroup"
          aria-label="Основной DXF"
        >
          {package_.entries.map((entry) => (
            <Radio
              key={entry.path}
              name={groupName}
              value={entry.path}
              checked={primary === entry.path}
              disabled={busy}
              label={entry.path}
              onChange={() => setPrimary(entry.path)}
            />
          ))}
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
          <Button
            variant="primary"
            disabled={busy || !primary}
            onClick={() => onStart(selection(package_, primary))}
          >
            Проверить комплект
          </Button>
          <Button variant="secondary" disabled={busy} onClick={reset}>
            Заменить
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="grid gap-2">
      <FileDropzone
        label="Загрузить DXF и AutoCAD snapshot"
        accept={ACCEPT}
        maxSize={MAX_FILE_BYTES}
        maxFiles={MAX_FILES}
        multiple
        disabled={busy || upload.isPending}
        onFilesAccepted={acceptFiles}
        onFilesRejected={() =>
          setClientError('Выберите DXF и парные AutoCAD snapshot.')
        }
        error={
          clientError || upload.error ? (
            <InlineMessage tone="error">
              {clientError ?? upload.error?.message}
            </InlineMessage>
          ) : undefined
        }
        message="Для каждого DXF выберите соседний .snapshot.json."
      >
        <strong className="text-sm leading-5 font-semibold">
          Перетащите комплект сюда
        </strong>
      </FileDropzone>
      {upload.isPending && <Progress label="Сохраняем комплект" />}
    </div>
  );
}
