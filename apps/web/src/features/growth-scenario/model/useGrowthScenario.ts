/** Чтение прогноза с отменой устаревших запросов; ползунок не выполняет записи */
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Project } from '@green/api-client';
import { errorMessage } from '@/shared/errors/errorMessage';

export function useGrowthScenario(
  project: Project | undefined,
  year: number,
  open: boolean,
) {
  const client = useQueryClient();
  const [requestedYear, setRequestedYear] = useState(year);
  useEffect(() => {
    const timer = setTimeout(() => setRequestedYear(year), 160);
    return () => clearTimeout(timer);
  }, [year]);
  const hasObjects = Boolean(
    project?.map_ready &&
    project.plan?.objects?.length &&
    !project.source_review,
  );
  const legacy = Boolean(
    (project?.plan?.planning_horizon_year ?? 0) !== 0 ||
    (project?.plan?.validation_basis?.planning_horizon_year ?? 0) !== 0,
  );
  const options = (value: number, enabled: boolean) => ({
    queryKey: [
      'growth-scenario',
      project?.id,
      project?.plan?.version,
      project?.geometry_version,
      project?.state_version,
      value,
    ],
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      api.getGrowthForecast(project!.id!, value, signal),
    enabled: hasObjects && enabled,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 60_000,
    retry: false as const,
  });
  const baseline = useQuery(options(0, open || legacy));
  const future = useQuery(
    options(
      requestedYear,
      open && requestedYear !== 0 && requestedYear === year,
    ),
  );
  useEffect(() => {
    if (
      (!open && (requestedYear !== 0 || !legacy)) ||
      (requestedYear !== 0 && requestedYear !== year)
    )
      void client.cancelQueries({
        queryKey: [
          'growth-scenario',
          project?.id,
          project?.plan?.version,
          project?.geometry_version,
          project?.state_version,
          requestedYear,
        ],
        exact: true,
      });
  }, [
    client,
    open,
    legacy,
    year,
    requestedYear,
    project?.id,
    project?.plan?.version,
    project?.geometry_version,
    project?.state_version,
  ]);
  const query = requestedYear === 0 ? baseline : future;
  const result = query.data;
  const matchesBasis = (value: typeof result) =>
    value?.plan_version === project?.plan?.version &&
    value?.geometry_version === project?.geometry_version;
  const baselineCurrentIssues =
    baseline.data?.year === 0 && matchesBasis(baseline.data)
      ? baseline.data.current_issues
      : undefined;
  const selectedCurrentIssues =
    matchesBasis(result) && result?.year === requestedYear
      ? result?.current_issues
      : undefined;
  const currentIssues = baselineCurrentIssues ?? selectedCurrentIssues;
  const requestedCurrent = hasObjects && (open || legacy);
  const actualYear = requestedYear === year;
  const data =
    matchesBasis(result) && result?.year === year ? result : undefined;
  return {
    data,
    currentIssues,
    currentPending:
      requestedCurrent && currentIssues === undefined && !baseline.error,
    currentError:
      requestedCurrent && currentIssues === undefined && baseline.error
        ? errorMessage(baseline.error)
        : undefined,
    retryCurrent: () => void baseline.refetch(),
    pending: open && hasObjects && !data && (!actualYear || !query.error),
    error:
      open && actualYear && query.error ? errorMessage(query.error) : undefined,
    retry: () => void query.refetch(),
  };
}
