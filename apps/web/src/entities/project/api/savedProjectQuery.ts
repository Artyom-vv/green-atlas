import { api } from '@green/api-client';
import {
  queryOptions,
  type QueryClient,
  type QueryKey,
} from '@tanstack/react-query';
import { validateAcknowledgedProjectRead } from '../model/acknowledgedRead';

/** Все потребители сохранённого проекта используют одну политику чтения и версий */
export const savedProjectQuery = (projectId: string) =>
  queryOptions({
    queryKey: ['workspace-project', projectId],
    queryFn: async ({ client, signal }) =>
      validateAcknowledgedProjectRead(
        client,
        projectId,
        await api.getProjectRead(projectId, false, signal),
      ),
    enabled: Boolean(projectId),
    staleTime: 0,
    refetchOnMount: 'always',
    networkMode: 'always',
    retry: false,
  });

/** Первичное pending чтение не отменяется обычным refetch: повтор явно завершает
 * прежний запрос, чтобы его поздний ответ не заменил результат нового чтения */
export async function restartProjectRead(
  client: QueryClient,
  queryKey: QueryKey,
) {
  await client.cancelQueries({ queryKey, exact: true });
  await client.refetchQueries({ queryKey, exact: true, type: 'active' });
}
