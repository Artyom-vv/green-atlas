import { ApiClientError } from '@green/api-client';

export const sourceMappingErrorFixture = () =>
  new ApiClientError('VALIDATION_ERROR', 'Проверьте заполнение полей', {
    'mappings.0': ['Value error, Категория не соответствует расчётной роли'],
  });
