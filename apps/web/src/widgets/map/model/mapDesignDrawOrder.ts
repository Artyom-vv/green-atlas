/** Ground covers stay below vegetation; outlines and labels stay above fills. */
export function mapDesignFillOrder(kind: string | undefined): number {
  if (kind === 'lawn') return -3;
  if (kind === 'existing_green') return -1.5;
  return -2;
}
