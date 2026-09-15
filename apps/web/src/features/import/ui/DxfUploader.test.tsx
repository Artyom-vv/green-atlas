import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { DxfUploader } from './DxfUploader';
import {
  MAX_DXF_UPLOAD_BYTES,
  MAX_RELEASE_BUNDLE_UPLOAD_BYTES,
} from '../model/uploadPolicy';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function transfer(files: File[]) {
  return {
    files,
    items: files.map((file) => ({
      kind: 'file',
      type: file.type,
      getAsFile: () => file,
    })),
    types: ['Files'],
  };
}

it('does not submit a dropped file while an upload is in progress', async () => {
  const onUpload = vi.fn();
  render(<DxfUploader loading onUpload={onUpload} />);
  const dropzone = screen.getByRole('button', { name: 'Выбрать DXF или ZIP' });
  await act(async () => {
    fireEvent.drop(dropzone, {
      dataTransfer: transfer([new File(['dxf'], 'site.dxf')]),
    });
  });
  expect(onUpload).not.toHaveBeenCalled();
  expect(dropzone).toHaveAttribute('aria-disabled', 'true');
  expect(screen.getByText('Загружаем файл')).toBeVisible();
});

it('uses file-neutral wording and displays the upload error once', () => {
  render(
    <DxfUploader loading={false} onUpload={vi.fn()} error="Архив повреждён" />,
  );
  expect(screen.getByText('Не удалось загрузить файл')).toBeVisible();
  expect(screen.getAllByText('Архив повреждён')).toHaveLength(1);
  expect(screen.queryByText('Ошибка DXF')).not.toBeInTheDocument();
});

it('accepts a dropped ZIP using the same command as a selected file', async () => {
  const onUpload = vi.fn();
  render(<DxfUploader loading={false} onUpload={onUpload} />);
  const file = new File(['zip'], 'release.zip', { type: 'application/zip' });
  await act(async () => {
    fireEvent.drop(
      screen.getByRole('button', { name: 'Выбрать DXF или ZIP' }),
      {
        dataTransfer: transfer([file]),
      },
    );
  });
  expect(onUpload).toHaveBeenCalledExactlyOnceWith(file);
});

it.each([
  ['large.dxf', MAX_DXF_UPLOAD_BYTES + 1, 'DXF должен быть не больше 50 МБ.'],
  [
    'large.zip',
    MAX_RELEASE_BUNDLE_UPLOAD_BYTES + 1,
    'ZIP-пакет должен быть не больше 120 МБ.',
  ],
])(
  'explains the format-specific size limit for %s',
  async (name, size, error) => {
    const onUpload = vi.fn();
    const { container } = render(
      <DxfUploader loading={false} onUpload={onUpload} />,
    );
    const file = new File(['sample'], name);
    Object.defineProperty(file, 'size', { value: size });
    await act(async () => {
      fireEvent.change(container.querySelector('input[type="file"]')!, {
        target: { files: [file] },
      });
    });
    expect(onUpload).not.toHaveBeenCalled();
    expect(screen.getByText(error)).toBeVisible();
  },
);

it('allows selecting the same file again after an upload error', async () => {
  const onUpload = vi.fn();
  const view = render(<DxfUploader loading={false} onUpload={onUpload} />);
  const file = new File(['dxf'], 'retry.dxf');
  const input = view.container.querySelector('input[type="file"]')!;
  await act(async () => fireEvent.change(input, { target: { files: [file] } }));
  view.rerender(
    <DxfUploader loading={false} onUpload={onUpload} error="Нет связи" />,
  );
  await act(async () => fireEvent.change(input, { target: { files: [file] } }));
  expect(onUpload).toHaveBeenCalledTimes(2);
  expect(onUpload).toHaveBeenLastCalledWith(file);
});

it('opens the shared chooser once through the keyboard', () => {
  const { container } = render(
    <DxfUploader loading={false} onUpload={vi.fn()} />,
  );
  const input =
    container.querySelector<HTMLInputElement>('input[type="file"]')!;
  const open = vi.spyOn(input, 'click').mockImplementation(() => undefined);
  const dropzone = screen.getByRole('button', { name: 'Выбрать DXF или ZIP' });
  dropzone.focus();
  fireEvent.keyDown(dropzone, { key: 'Enter', keyCode: 13 });
  expect(open).toHaveBeenCalledOnce();
});
