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

/** Filter generated SVG groups without constructing a DOM for the whole CAD drawing. */
export function visibleOverviewSvg(
  svg: string,
  hiddenNames: readonly string[],
): string {
  if (!hiddenNames.length) return svg;
  const hidden = new Set(hiddenNames);
  return svg.replace(/<g\b([^>]*)>[\s\S]*?<\/g>/g, (group, attributes: string) => {
    const encodedName = /\bdata-source-layer=(["'])(.*?)\1/.exec(attributes)?.[2];
    if (!encodedName) return group;
    const name = encodedName
      .replace(/&#(x[0-9a-f]+|[0-9]+);/gi, (_, code: string) =>
        String.fromCodePoint(code[0].toLowerCase() === 'x'
          ? Number.parseInt(code.slice(1), 16) : Number(code)),
      )
      .replace(/&quot;/g, '"')
      .replace(/&apos;/g, "'")
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&amp;/g, '&');
    return hidden.has(name) ? '' : group;
  });
}
