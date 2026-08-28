export function repeatedItemLabel<T extends { label: string }>(items: T[], item: T) {
  const matches = items.filter((candidate) => candidate.label === item.label);
  if (matches.length < 2) return item.label;
  return `${item.label} ${matches.indexOf(item) + 1}`;
}
