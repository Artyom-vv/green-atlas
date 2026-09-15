import type { FC, ReactNode } from 'react';
import { useState } from 'react';
import {
  useDropzone,
  type DropzoneOptions,
  type FileRejection,
} from 'react-dropzone';
import { tv } from 'tailwind-variants';

const dropzone = tv({
  base: 'rounded-control data-[invalid]:border-error flex min-h-28 min-w-0 cursor-pointer flex-col items-center justify-center gap-2 border border-dashed border-neutral-300 bg-white px-4 py-5 text-center text-sm text-neutral-700 transition-colors hover:border-blue-500 hover:bg-blue-100 focus-visible:outline-2 focus-visible:outline-(--focus) data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50',
  variants: { active: { true: 'border-blue-600 bg-blue-100' } },
});
export interface FileDropzoneProps extends Pick<
  DropzoneOptions,
  | 'accept'
  | 'minSize'
  | 'maxSize'
  | 'maxFiles'
  | 'multiple'
  | 'disabled'
  | 'validator'
> {
  label?: string;
  error?: ReactNode;
  message?: ReactNode;
  className?: string;
  onFilesAccepted: (files: File[]) => void;
  onFilesRejected?: (rejections: FileRejection[]) => void;
  children?: ReactNode;
}
export const FileDropzone: FC<FileDropzoneProps> = ({
  label = 'Загрузить файл',
  multiple = false,
  disabled = false,
  error,
  message,
  className,
  onFilesAccepted,
  onFilesRejected,
  children,
  ...options
}) => {
  const [rejected, setRejected] = useState(false);
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    ...options,
    multiple,
    disabled,
    onDropAccepted: (files) => {
      setRejected(false);
      onFilesAccepted(files);
    },
    onDropRejected: (rejections) => {
      setRejected(true);
      onFilesRejected?.(rejections);
    },
  });
  const displayedError =
    error ??
    (rejected ? 'Выбранный файл не соответствует требованиям.' : undefined);
  return (
    <div className="grid min-w-0 gap-2">
      <div
        {...getRootProps({
          role: 'button',
          'aria-label': label,
          'aria-disabled': disabled || undefined,
          'aria-invalid': displayedError ? true : undefined,
          'data-slot': 'file-dropzone',
          'data-disabled': disabled || undefined,
          'data-invalid': displayedError ? true : undefined,
          className: dropzone({ active: isDragActive, className }),
        })}
      >
        <input {...getInputProps()} />
        {children ?? (
          <>
            <span className="font-medium">
              {isDragActive ? 'Отпустите файл' : 'Перетащите файл сюда'}
            </span>
            <span className="text-xs text-neutral-500">
              или выберите файл с устройства
            </span>
          </>
        )}
      </div>
      {displayedError ? (
        <div className="text-error text-xs" role="alert">
          {displayedError}
        </div>
      ) : message ? (
        <span className="text-xs text-neutral-500">{message}</span>
      ) : null}
    </div>
  );
};
