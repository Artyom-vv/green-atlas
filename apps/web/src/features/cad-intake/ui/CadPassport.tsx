import type { CadPackagePassport } from '@green/api-client';
import { Disclosure, InlineMessage, ScrollArea, Text } from '@green/ui';

const referenceLabels = {
  resolved: 'Найдена',
  missing: 'Не найдена',
  outside_package: 'Вне комплекта',
  ambiguous: 'Несколько версий',
  cycle: 'Циклическая ссылка',
};

export function CadPassport({
  passport,
  historical = false,
}: {
  passport: CadPackagePassport;
  historical?: boolean;
}) {
  const readable = passport.drawings.filter(
    (drawing) => drawing.status === 'readable',
  ).length;
  const resolved = passport.references.filter(
    (reference) => reference.status === 'resolved',
  ).length;
  return (
    <section className="grid min-w-0 gap-3" aria-label="Паспорт комплекта">
      <Text as="h3" variant="heading">
        Паспорт комплекта
      </Text>
      <Text as="p">
        Прочитано чертежей: {readable} из {passport.drawings.length}. Найдено
        ссылок: {resolved} из {passport.references.length}.
      </Text>
      <InlineMessage tone="warning">
        {historical
          ? 'Паспорт относится к предыдущей версии проекта. Повторите проверку комплекта.'
          : passport.status === 'blocked'
            ? 'Есть замечания к комплекту. Решения можно выбрать в мастере открытия.'
            : 'Состав проверен. Назначение слоёв можно сверить после открытия.'}
      </InlineMessage>
      {!!passport.blockers.length && (
        <Disclosure title={`Проблемы (${passport.blockers.length})`}>
          <ul className="m-0 grid list-disc gap-2 pl-5 text-xs">
            {passport.blockers.map((blocker, index) => (
              <li key={index}>{blocker}</li>
            ))}
          </ul>
        </Disclosure>
      )}
      <Disclosure title={`Чертежи (${passport.drawings.length})`}>
        <ScrollArea viewportClassName="max-h-64" contentClassName="grid gap-3">
          {passport.drawings.map((drawing) => (
            <div key={drawing.path} className="grid min-w-0 gap-1">
              <Text as="p" className="break-words">
                {drawing.path}
              </Text>
              <Text variant="caption">
                {drawing.status === 'readable' ? 'Прочитан' : 'Не прочитан'}
                {drawing.message && `: ${drawing.message}`}
              </Text>
            </div>
          ))}
        </ScrollArea>
      </Disclosure>
      {!!passport.references.length && (
        <Disclosure title={`Внешние ссылки (${passport.references.length})`}>
          <ScrollArea
            viewportClassName="max-h-64"
            contentClassName="grid gap-3"
          >
            {passport.references.map((reference) => (
              <div
                key={`${reference.owner}:${reference.block}`}
                className="grid min-w-0 gap-1"
              >
                <Text as="p" className="break-words">
                  {reference.requested_path || reference.block}
                </Text>
                <Text variant="caption">
                  {referenceLabels[reference.status]}
                  {reference.resolution === 'explicit_override' &&
                    ' — Назначена явно'}
                </Text>
                <Text variant="caption" className="break-words">
                  Из {reference.owner}
                </Text>
              </div>
            ))}
          </ScrollArea>
        </Disclosure>
      )}
    </section>
  );
}
