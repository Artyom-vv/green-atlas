import { useLayoutEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Dialog, Text } from '@green/ui';
import { useLocalRuntime } from '@/shared/desktop/useLocalRuntime';

const readKeys = new Set([
  'workspace-project',
  'setup-project',
  'import-project-read',
  'projects',
  'plan-history',
  'data-passport',
  'cad-transfer-review',
  'operation',
  'latest-operation',
  'source-object-review',
  'source-object-context',
  'source-read-issues',
  'native-faces',
  'native-area-preview',
  'layer-recognition',
  'species',
  'assortment',
  'placement-masks',
  'building-screen-targets',
  'plan-scene',
  'map-features',
  'workspace-cad-source',
  'project-release',
  'preparation-completion',
]);

/** Keep the existing document and its drafts while the native host restores its
 * authenticated runtime. Restoration refreshes reads, never uncertain writes. */
export function LocalRuntimeBoundary() {
  const available = useLocalRuntime();
  const previous = useRef(available);
  const client = useQueryClient();
  useLayoutEffect(() => {
    if (available && !previous.current)
      void client.invalidateQueries({
        predicate: (query) => readKeys.has(String(query.queryKey[0])),
      });
    previous.current = available;
  }, [available, client]);
  return (
    <Dialog
      open={!available}
      title="Локальный сервис остановлен"
      header={
        <Text as="h2" variant="heading" className="m-0 px-4 py-3">
          Локальный сервис остановлен
        </Text>
      }
      onClose={() => undefined}
    >
      <Text as="p">
        Текущая работа остаётся в этом окне. Перезапустите локальный сервис
        через меню приложения и дождитесь подтверждения соединения.
      </Text>
      <Text as="p">
        После подключения проверьте результат незавершённого сохранения.
        Приложение не отправляет его повторно.
      </Text>
    </Dialog>
  );
}
