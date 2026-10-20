/** Object property order is irrelevant; array order keeps its existing meaning. */
export const canonicalMappingValue = (value: unknown): unknown =>
  Array.isArray(value)
    ? value.map(canonicalMappingValue)
    : value && typeof value === 'object'
      ? Object.fromEntries(
          Object.entries(value)
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([key, entry]) => [key, canonicalMappingValue(entry)]),
        )
      : value;
