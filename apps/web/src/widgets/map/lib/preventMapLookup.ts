/** Keep macOS Force Touch's dictionary hit test off the map interaction surface. */
export function preventMapLookup(target: HTMLElement): () => void {
  const prevent = (event: Event) => event.preventDefault();
  target.addEventListener('webkitmouseforcewillbegin', prevent);
  return () => target.removeEventListener('webkitmouseforcewillbegin', prevent);
}
