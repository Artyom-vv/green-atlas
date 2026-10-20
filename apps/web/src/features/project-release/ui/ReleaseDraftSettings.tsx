import { Button } from '@green/ui';

export function ReleaseDraftSettings({
  storageAvailable = true,
  loading,
  onClear,
}: {
  storageAvailable?: boolean;
  loading?: boolean;
  onClear: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 text-neutral-600">
      <span role="status" className={storageAvailable ? 'sr-only' : undefined}>
        {loading
          ? 'Собираем пакет…'
          : storageAvailable
            ? 'Настройки сохранены в этой вкладке'
            : 'Настройки сохранены до закрытия страницы'}
      </span>
      <Button
        aria-label="Сбросить настройки"
        variant="ghost"
        disabled={loading}
        onClick={onClear}
      >
        Сбросить
      </Button>
    </div>
  );
}
