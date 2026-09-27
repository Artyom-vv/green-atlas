import { useState, type ComponentProps, type FC } from 'react';
import { FileDropzone, InlineMessage, Progress } from '@green/ui';
import {
  MAX_RELEASE_BUNDLE_UPLOAD_BYTES,
  PROJECT_IMPORT_ACCEPT,
  validateProjectUpload,
} from '../model/uploadPolicy';

export interface DxfUploaderProps {
  onUpload: (file: File) => void;
  loading: boolean;
  error?: string;
}

type DropzoneProps = ComponentProps<typeof FileDropzone>;

const validateFile: DropzoneProps['validator'] = (file) => {
  const message = validateProjectUpload(file);
  return message ? { code: 'project-upload-policy', message } : null;
};

export const DxfUploader: FC<DxfUploaderProps> = ({
  onUpload,
  loading,
  error,
}) => {
  const [clientError, setClientError] = useState<string>();
  const acceptFiles: DropzoneProps['onFilesAccepted'] = (files) => {
    const file = files[0];
    if (!file || loading) return;
    setClientError(undefined);
    onUpload(file);
  };
  const rejectFiles: DropzoneProps['onFilesRejected'] = (rejections) => {
    if (loading) return;
    const first = rejections[0];
    if (!first) return;
    setClientError(
      validateProjectUpload(first.file) ?? 'Выберите один файл DXF или ZIP.',
    );
  };

  return (
    <div className="grid gap-3">
      <FileDropzone
        label="Выбрать DXF или ZIP"
        accept={PROJECT_IMPORT_ACCEPT}
        maxSize={MAX_RELEASE_BUNDLE_UPLOAD_BYTES}
        multiple={false}
        disabled={loading}
        validator={validateFile}
        onFilesAccepted={acceptFiles}
        onFilesRejected={rejectFiles}
        error={
          clientError || error ? (
            <InlineMessage tone="error" title="Не удалось загрузить файл">
              {clientError ?? error}
            </InlineMessage>
          ) : undefined
        }
        message="или выберите файл на компьютере: DXF до 50 МБ, ZIP до 120 МБ"
      >
        <strong className="text-sm leading-5 font-semibold">
          Перетащите DXF или ZIP-пакет сюда
        </strong>
      </FileDropzone>
      {loading && <Progress label="Загружаем файл" />}
    </div>
  );
};
