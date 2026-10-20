import { HTTP_FAILURE_MESSAGE } from './policy';

export class ApiClientError extends Error {
  constructor(
    public code: string,
    message: string,
    public fieldErrors: Record<string, string[]> = {},
    public details: Record<string, unknown> = {},
    public status?: number,
  ) {
    super(message);
  }
}

interface ErrorPayload {
  code?: string;
  message?: string;
  field_errors?: Record<string, string[]>;
  details?: Record<string, unknown>;
}

interface HttpErrorPayload extends ErrorPayload {
  detail?: string | ErrorPayload;
}

export async function responseError(
  response: Response,
): Promise<ApiClientError> {
  const payload = (await response
    .json()
    .catch(() => null)) as HttpErrorPayload | null;
  const error = payload?.detail ?? payload;
  if (typeof error === 'string')
    return new ApiClientError('HTTP_ERROR', error, {}, {}, response.status);
  return new ApiClientError(
    error?.code ?? 'HTTP_ERROR',
    error?.message ?? HTTP_FAILURE_MESSAGE(response.status),
    error?.field_errors,
    error?.details,
    response.status,
  );
}
