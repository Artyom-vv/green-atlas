import { readExcerpt } from '@/features/assistant/model/autonomous/presentation';
import { AssistantCard } from '@/features/assistant/ui/shared/AssistantCard';
import { Disclosure, Text } from '@green/ui';
import type { FC } from 'react';
import { ReadEvidence, SpeciesOptionRow } from '../ReadEvidence';
import type { ReadResultsProps } from './ReadResults.types';
export interface ShortlistResultProps extends Pick<
  ReadResultsProps,
  'shortlist' | 'project'
> {}
export const ShortlistResult: FC<ShortlistResultProps> = ({
  shortlist,
  project,
}) => (
  <>
    {!!shortlist && (
      <AssistantCard aria-label="Подбор пород" tone="neutral">
        <span className="text-xs font-medium text-neutral-600">
          Результат подбора
        </span>
        <Text as="h3" variant="heading" className="m-0 wrap-anywhere">
          {shortlist.total
            ? `Варианты пород: ${shortlist.total}`
            : 'Варианты не найдены'}
        </Text>
        <ReadEvidence read={shortlist.read} project={project} />
        <ul className="m-0 grid list-none gap-2 p-0">
          {shortlist.items.slice(0, 3).map((item, index) => (
            <SpeciesOptionRow key={item.id ?? index} item={item} />
          ))}
        </ul>
        {shortlist.items.length > 3 && (
          <Disclosure
            variant="plain"
            title={<>Ещё вариантов: {shortlist.items.length - 3}</>}
          >
            <ul className="m-0 grid list-none gap-2 p-0">
              {shortlist.items.slice(3).map((item, index) => (
                <SpeciesOptionRow key={item.id ?? index} item={item} />
              ))}
            </ul>
          </Disclosure>
        )}
        {readExcerpt(
          shortlist.total,
          shortlist.items.length,
          shortlist.read,
        ) ? (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            {readExcerpt(
              shortlist.total,
              shortlist.items.length,
              shortlist.read,
            )}
          </Text>
        ) : shortlist.total > shortlist.items.length ? (
          <Text as="p" variant="body" className="m-0 wrap-anywhere">
            В результате показано {shortlist.items.length} из {shortlist.total}{' '}
            вариантов.
          </Text>
        ) : null}
        <Text className="text-xs text-neutral-600" as="p" variant="body">
          {shortlist.read.caveat ??
            'Это подбор из каталога. Расстановка и отступы проверяются при расчёте размещения.'}
        </Text>
      </AssistantCard>
    )}
  </>
);
