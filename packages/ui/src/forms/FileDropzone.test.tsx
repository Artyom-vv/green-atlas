import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { FileDropzone } from '../index';

const drop = (target: HTMLElement, file: File) =>
  fireEvent.drop(target, {
    dataTransfer: {
      files: [file],
      items: [{ kind: 'file', type: file.type, getAsFile: () => file }],
      types: ['Files'],
    },
  });

describe('FileDropzone', () => {
  it('passes accepted files without interpreting their domain', async () => {
    const onFilesAccepted = vi.fn();
    render(
      <FileDropzone
        label="Выбрать документ"
        onFilesAccepted={onFilesAccepted}
      />,
    );
    const file = new File(['payload'], 'project.custom');
    drop(screen.getByRole('button', { name: 'Выбрать документ' }), file);
    await waitFor(() => expect(onFilesAccepted).toHaveBeenCalledWith([file]));
  });
  it('lets the caller enforce policy and explain a rejected file', async () => {
    const onFilesAccepted = vi.fn(),
      onFilesRejected = vi.fn();
    const { rerender } = render(
      <FileDropzone
        onFilesAccepted={onFilesAccepted}
        onFilesRejected={onFilesRejected}
        validator={() => ({
          code: 'caller-policy',
          message: 'Неподдерживаемая версия',
        })}
      />,
    );
    drop(screen.getByRole('button'), new File(['x'], 'data.txt'));
    await waitFor(() => expect(onFilesRejected).toHaveBeenCalledOnce());
    expect(onFilesAccepted).not.toHaveBeenCalled();
    rerender(
      <FileDropzone
        onFilesAccepted={onFilesAccepted}
        error={<div>Откройте версию 2</div>}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Откройте версию 2');
  });
  it('blocks selection while disabled', async () => {
    const onFilesAccepted = vi.fn();
    render(<FileDropzone disabled onFilesAccepted={onFilesAccepted} />);
    const root = screen.getByRole('button');
    expect(root).toHaveAttribute('aria-disabled', 'true');
    drop(root, new File(['x'], 'data.txt'));
    await Promise.resolve();
    expect(onFilesAccepted).not.toHaveBeenCalled();
  });
});
