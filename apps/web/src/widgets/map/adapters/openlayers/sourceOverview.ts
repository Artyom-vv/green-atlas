export interface SourceOverview {
  complete: boolean;
  svg: string;
  extent: [number, number, number, number];
  source_features: number;
  rendered_features: number;
}

export function readSourceOverview(
  geometry?: Record<string, unknown>,
): SourceOverview | undefined {
  const overview = geometry?.source_overview as
    Partial<SourceOverview> | undefined;
  if (
    overview?.complete !== true ||
    typeof overview.svg !== 'string' ||
    !Array.isArray(overview.extent) ||
    overview.extent.length !== 4 ||
    !overview.extent.every(Number.isFinite)
  )
    return;
  return overview as SourceOverview;
}

/** Modify inert image markup, never inject source names or SVG into page DOM. */
export function visibleOverviewSvg(
  svg: string,
  hiddenNames: readonly string[],
): string {
  if (!hiddenNames.length) return svg;
  const document = new DOMParser().parseFromString(svg, 'image/svg+xml');
  const hidden = new Set(hiddenNames);
  for (const group of document.querySelectorAll('[data-source-layer]')) {
    if (hidden.has(group.getAttribute('data-source-layer') ?? ''))
      group.remove();
  }
  return new XMLSerializer().serializeToString(document);
}
