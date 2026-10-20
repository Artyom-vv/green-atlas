import { describe, expect, it } from 'vitest';
import { ApiClientError, responseError } from './errors';

describe('HTTP error compatibility', () => {
  it.each([false, true])(
    'preserves structured conflict details, nested=%s',
    async (nested) => {
      const error = {
        code: 'PROJECT_VERSION_CONFLICT',
        message: 'Проект изменён',
        field_errors: { count: ['Количество изменено'] },
        details: { expected_version: 7, current_version: 8 },
      };
      const response = new Response(
        JSON.stringify(nested ? { detail: error } : error),
        { status: 409 },
      );
      const result = await responseError(response);
      expect(result).toBeInstanceOf(ApiClientError);
      expect(result).toMatchObject({
        code: error.code,
        message: error.message,
        fieldErrors: error.field_errors,
        details: error.details,
        status: 409,
      });
    },
  );

  it('preserves a textual FastAPI error and handles a non-JSON proxy response', async () => {
    const detail = await responseError(
      new Response(JSON.stringify({ detail: 'Укажите участок' }), {
        status: 422,
      }),
    );
    expect(detail.message).toBe('Укажите участок');
    expect(detail.code).toBe('HTTP_ERROR');
    expect(detail.status).toBe(422);
    const proxy = await responseError(
      new Response('<h1>Bad gateway</h1>', { status: 502 }),
    );
    expect(proxy.message).toBe('Ошибка запроса: 502');
    expect(proxy.fieldErrors).toEqual({});
    expect(proxy.status).toBe(502);
  });

  it('keeps existing manually constructed errors compatible without a response status', () => {
    const error = new ApiClientError('LOCAL_ERROR', 'Local validation failed', {
      count: ['Required'],
    });
    expect(error.status).toBeUndefined();
    expect(error.fieldErrors).toEqual({ count: ['Required'] });
    expect(error.details).toEqual({});
  });
});
