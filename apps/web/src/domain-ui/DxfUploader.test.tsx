import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { DxfUploader } from './DxfUploader';

afterEach(cleanup);

it('does not submit a second dropped file while an upload is in progress', () => {
  const onUpload = vi.fn();
  const { container } = render(<DxfUploader loading onUpload={onUpload} />);
  fireEvent.drop(container.querySelector('.dxf-dropzone')!, { dataTransfer: { files: [new File(['dxf'], 'site.dxf')] } });
  expect(onUpload).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Выбрать DXF или ZIP' })).toBeDisabled();
  expect(screen.getByText('Загружаем файл')).toBeVisible();
});

it('uses file-neutral wording for ZIP errors', () => {
  render(<DxfUploader loading={false} onUpload={vi.fn()} error="Архив повреждён" />);
  expect(screen.getByText('Не удалось загрузить файл')).toBeVisible();
  expect(screen.queryByText('Ошибка DXF')).not.toBeInTheDocument();
});
