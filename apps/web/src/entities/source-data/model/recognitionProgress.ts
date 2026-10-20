import type { Layer, LayerRecognition } from '@green/api-client';
import { formatDuration } from '@/shared/time/duration';
import type { RecognitionPhase } from '../config/sourceMessages';
import { RECOGNITION_MESSAGES } from '../config/sourceMessages';

/** Сводка и принятие используют только точные ответы сохранённого checkpoint */
export function completedRecognitionLayerIds(
  layers: Layer[],
  recognition?: LayerRecognition,
): Set<string> | undefined {
  if (!recognition || !['running', 'failed'].includes(recognition.status))
    return undefined;
  const completed = new Set(recognition.completed_layer_ids ?? []);
  const sourceIds = new Set(layers.map((layer) => layer.id));
  return completed.size > 0 &&
    completed.size === recognition.completed_layer_ids?.length &&
    completed.size === recognition.processed_count &&
    recognition.total_count === layers.length &&
    [...completed].every((id) => sourceIds.has(id))
    ? completed
    : new Set();
}

/** Имена относятся только к текущему пакету API; отдельный слой не имеет выдуманного таймера. */
export function recognitionTask(
  title: string,
  recognition: LayerRecognition | undefined,
  layers: Layer[] = [],
) {
  if (
    recognition?.status === 'running' &&
    recognition.message === RECOGNITION_MESSAGES.reconnecting
  )
    return RECOGNITION_MESSAGES.reconnecting;
  if (
    recognition?.status !== 'running' ||
    !['classifying', 'reviewing_conflicts'].includes(recognition.stage ?? '')
  )
    return title;
  const ids = recognition.active_layer_ids ?? [];
  const name = layers.find((layer) => layer.id === ids[0])?.source_name;
  return name
    ? `${RECOGNITION_MESSAGES.layerTask(name)}${ids.length > 1 ? RECOGNITION_MESSAGES.moreLayers(ids.length - 1) : ''}`
    : title;
}

export interface RecognitionProgressInput {
  recognition?: LayerRecognition;
  loading?: boolean;
  error?: boolean;
  retrying?: boolean;
  checkingStatus?: boolean;
  saving?: boolean;
  noKnownJob?: boolean;
  assignmentsSaved?: boolean;
}

/** Этап приходит из API; счётчик первого прохода не означает завершение всей задачи. */
export function recognitionProgress(input: RecognitionProgressInput) {
  const { recognition } = input;
  const primaryDone = recognition?.processed_count ?? 0;
  const primaryTotal = recognition?.total_count ?? 0;
  const stage = recognition?.stage;
  const countingStage =
    recognition?.status !== 'completed' &&
    (stage === 'classifying' || stage === 'reviewing_conflicts');
  const done = countingStage
    ? (recognition?.stage_processed_count ?? 0)
    : primaryDone;
  const total = countingStage
    ? (recognition?.stage_total_count ?? 0)
    : primaryTotal;
  const phase = recognitionPhase(input);
  return {
    phase,
    done,
    total,
    primaryDone,
    primaryTotal,
    value:
      (phase === 'classifying' || phase === 'reviewing') && total > 0
        ? (done / total) * 100
        : undefined,
    busy: [
      'checking',
      'loading',
      'resuming',
      'saving',
      'finishing',
      'classifying',
      'queued',
      'reviewing',
      'publishing',
    ].includes(phase),
  };
}

function recognitionPhase({
  recognition,
  loading,
  error,
  retrying,
  checkingStatus,
  saving,
  noKnownJob,
  assignmentsSaved,
}: RecognitionProgressInput): RecognitionPhase {
  if (checkingStatus) return 'checking';
  if (saving) return 'saving';
  if (error) return 'unknown';
  if (loading) return 'loading';
  if (retrying) return 'resuming';
  if (!recognition && assignmentsSaved) return 'completed';
  if (noKnownJob) return 'not_started';
  if (recognition?.status !== 'running')
    return recognition?.status ?? 'loading';
  switch (recognition.stage) {
    case 'queued':
      return 'queued';
    case 'reviewing_conflicts':
      return 'reviewing';
    case 'saving':
      return 'publishing';
    case 'classifying':
      return 'classifying';
    default:
      return (recognition.total_count ?? 0) > 0 &&
        (recognition.processed_count ?? 0) >= (recognition.total_count ?? 0)
        ? 'finishing'
        : 'classifying';
  }
}

/** Время относится к серверной задаче; терминальный результат фиксирует длительность. */
export function recognitionTiming(
  recognition: LayerRecognition | undefined,
  now: number,
) {
  const started = Date.parse(recognition?.started_at ?? '');
  const finished = Date.parse(recognition?.finished_at ?? '');
  const end = recognition?.status === 'running' ? now : finished;
  return {
    elapsed:
      Number.isFinite(started) && Number.isFinite(end)
        ? formatDuration(end - started)
        : undefined,
  };
}
