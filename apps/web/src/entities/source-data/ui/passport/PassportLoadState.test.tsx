import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PREPARATION_MESSAGES } from '../../config/sourceMessages';
import { PassportLoadState } from './PassportLoadState';

afterEach(cleanup);

describe('passport read state', () => {
  it('announces an indeterminate read without a fictitious progress value', () => {
    render(<PassportLoadState state="loading" />);
    const progress = screen.getByRole('progressbar', {
      name: PREPARATION_MESSAGES.readingPassport,
    });
    expect(progress).toBeVisible();
    expect(progress).not.toHaveAttribute('aria-valuenow');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('offers explicit recovery after failure without confirming data', () => {
    const retry = vi.fn();
    render(<PassportLoadState state="error" onRetry={retry} />);
    expect(screen.getByRole('alert')).toHaveTextContent(
      PREPARATION_MESSAGES.passportReadFailed,
    );
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(retry).toHaveBeenCalledTimes(1);
  });
});
