import { useRef, useState, type DragEvent } from 'react';
import { Upload } from 'lucide-react';
import { Button, InlineMessage, Progress, Surface } from '@green/ui';
import { validateDxfUpload } from './dxfUpload';

export function DxfUploader({ onUpload, loading, error }: { onUpload: (file: File) => void; loading: boolean; error?: string }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [clientError, setClientError] = useState<string>();

  const accept = (files: FileList | null) => {
    const file = files?.[0];
    if (!file || loading) return;
    const validationError = validateDxfUpload(file);
    if (validationError) {
      setClientError(validationError);
      return;
    }
    setClientError(undefined);
    onUpload(file);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    accept(event.dataTransfer.files);
  };

  return (
    <div className="dxf-uploader">
      <Surface className={`dxf-dropzone ${dragging ? 'is-dragging' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
        <input ref={inputRef} type="file" accept=".dxf,.zip,application/dxf,application/zip" hidden onChange={(event) => { accept(event.target.files); event.target.value = ''; }} />
        <strong>Перетащите DXF или ZIP-пакет сюда</strong>
        <span>или выберите файл на компьютере: DXF до 50 МБ, ZIP до 120 МБ</span>
        <Button variant="primary" icon={Upload} aria-label="Выбрать DXF или ZIP" disabled={loading} onClick={() => inputRef.current?.click()}>Выбрать файл</Button>
      </Surface>
      {loading ? <Progress label="Загружаем файл" /> : null}
      {clientError || error ? <InlineMessage tone="error" title="Не удалось загрузить файл">{clientError ?? error}</InlineMessage> : null}
    </div>
  );
}
