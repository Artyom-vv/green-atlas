import type { FC } from 'react';
import { IssuesResult } from './read/IssuesResult';
import type { ReadResultsProps } from './read/ReadResults.types';
import { ShortlistResult } from './read/ShortlistResult';
export type { ReadResultsProps } from './read/ReadResults.types';
export const ReadResults: FC<ReadResultsProps> = (props) => (
  <>
    <ShortlistResult shortlist={props.shortlist} project={props.project} />
    <IssuesResult issues={props.issues} project={props.project} />
  </>
);
