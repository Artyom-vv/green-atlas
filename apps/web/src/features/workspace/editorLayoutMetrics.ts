export type LayoutPreference = Partial<Record<'width' | 'projectHeight' | 'resultsHeight', number>>;

/** One sizing policy. Preferences are proportions of the actual workspace,
 * not fixed pixels. Rem-based limits only protect readable fields and map space. */
export function editorLayoutMetrics(container: { width: number; height: number; rem: number }, preference: LayoutPreference = {}) {
  const { width, height, rem } = container;
  const minWidth = Math.min(19 * rem, Math.max(0, width - 4 * rem));
  const maxWidth = Math.max(minWidth, Math.min(33 * rem, width * .44));
  const minProject = Math.min(8 * rem, height * .3);
  const maxProject = Math.max(minProject, height - 16 * rem);
  const minResults = Math.min(6 * rem, height * .25);
  const maxResults = Math.max(minResults, height * .55);
  const bounded = (value: number, min: number, max: number) => Math.round(Math.max(min, Math.min(max, value)));
  return { minWidth, maxWidth, minProject, maxProject, minResults, maxResults,
    width: bounded(width * (preference.width ?? .26), minWidth, maxWidth),
    projectHeight: bounded(height * (preference.projectHeight ?? .3), minProject, maxProject),
    resultsHeight: bounded(height * (preference.resultsHeight ?? .24), minResults, maxResults),
  };
}
