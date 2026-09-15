import { Button, InlineMessage, Progress, Text } from '@green/ui';
import type { IntakeSelection } from '../api/startIntake';
import { useCadDirectory } from '../model/useCadDirectory';
import { CadDirectoryList } from './CadDirectoryList';
import { CadDirectoryNavigation } from './CadDirectoryNavigation';

export function CadPackageBrowser({
  busy,
  onStart,
}: {
  busy: boolean;
  onStart: (selection: IntakeSelection) => void;
}) {
  const state = useCadDirectory();
  const { roots, directory, rootId, path, selected } = state;
  const error = roots.error ?? directory.error;
  return (
    <div className="grid min-w-0 gap-3">
      <Text as="p">
        Выберите основной чертёж. Проверим состав комплекта и внешние ссылки.
      </Text>
      {roots.isLoading && <Progress label="Читаем доступные комплекты" />}
      {roots.data?.length === 0 && (
        <InlineMessage>Подключённых комплектов пока нет.</InlineMessage>
      )}
      {error && (
        <InlineMessage tone="error">
          <div className="flex flex-wrap items-center gap-3">
            <span>{error.message}</span>
            <Button
              onClick={() => {
                if (rootId) void directory.refetch();
                else void roots.refetch();
              }}
            >
              Повторить
            </Button>
          </div>
        </InlineMessage>
      )}
      {!!roots.data?.length && (
        <>
          <CadDirectoryNavigation
            roots={roots.data}
            rootId={rootId}
            path={path}
            disabled={busy}
            onNavigate={state.navigate}
          />
          {directory.isLoading && <Progress label="Читаем папку" />}
          {directory.data && (
            <CadDirectoryList
              directory={directory.data}
              selected={selected}
              disabled={busy}
              onNavigate={state.navigate}
              onSelect={state.select}
            />
          )}
          {directory.data?.truncated && (
            <InlineMessage tone="warning">
              Каталог слишком большой; показана только его часть.
            </InlineMessage>
          )}
          <div className="grid gap-2 border-t border-neutral-200 pt-3">
            <Text variant="caption" className="truncate" title={selected}>
              {selected?.split('/').at(-1) ?? 'Выберите DWG или DXF в списке.'}
            </Text>
            <Button
              variant="primary"
              loading={busy}
              disabled={!selected || directory.isFetching}
              onClick={() => selected && onStart({ rootId, path: selected })}
            >
              Проверить комплект
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
