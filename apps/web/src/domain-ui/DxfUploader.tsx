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
    if (!file) return;
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
        <input ref={inputRef} type="file" accept=".dxf,application/dxf" hidden onChange={(event) => accept(event.target.files)} />
        <strong>Перетащите DXF сюда</strong>
        <span>или выберите файл на компьютере, до 50 МБ</span>
        <Button variant="primary" icon={Upload} disabled={loading} onClick={() => inputRef.current?.click()}>Выбрать DXF</Button>
      </Surface>
      {loading ? <Progress label="Проверяем DXF" /> : null}
      {clientError || error ? <InlineMessage tone="error" title="Ошибка DXF">{clientError ?? error}</InlineMessage> : null}
    </div>
  );
}
