import { useEffect, useMemo } from 'react';
import { useFormContext } from 'react-hook-form';
import type { SpeciesRevision, SpeciesShortlistItem } from '@green/api-client';
import type { PatternFormValues } from './patternForm';
interface PatternSpeciesOptions {
  values: PatternFormValues;
  species: SpeciesRevision[];
  shortlist?: SpeciesShortlistItem[];
  shortlistLoading?: boolean;
}
/** Resolves the current catalog revision inside the existing form owner. */
export function usePatternSpecies({
  values,
  species,
  shortlist,
  shortlistLoading,
}: PatternSpeciesOptions) {
  const { setValue } = useFormContext<PatternFormValues>();
  const shortlistSpecies = useMemo(
    () => shortlist?.map((item) => item.species),
    [shortlist],
  );
  const catalogSpecies = shortlistSpecies ?? species;
  const trees = useMemo(
    () => catalogSpecies.filter((item) => item.kind === 'tree'),
    [catalogSpecies],
  );
  const shrubs = useMemo(
    () => catalogSpecies.filter((item) => item.kind === 'shrub'),
    [catalogSpecies],
  );
  const plantKind = values.composition === 'shrubs' ? 'shrub' : 'tree';
  const availableSpecies = plantKind === 'shrub' ? shrubs : trees;
  const speciesId =
    plantKind === 'shrub' ? values.shrubSpeciesId : values.treeSpeciesId;
  const primaryField: 'shrubSpeciesId' | 'treeSpeciesId' =
    plantKind === 'shrub' ? 'shrubSpeciesId' : 'treeSpeciesId';

  // Reconcile catalog revisions in the one form owner, never a second draft.
  // A pending shortlist must not temporarily replace a user's chosen revision.
  useEffect(() => {
    if (shortlistLoading) return;
    if (
      !trees.some((item) => item.id === values.treeSpeciesId) &&
      values.treeSpeciesId !== trees[0]?.id
    ) {
      setValue('treeSpeciesId', trees[0]?.id);
    }
  }, [setValue, shortlistLoading, trees, values.treeSpeciesId]);
  useEffect(() => {
    if (shortlistLoading) return;
    if (
      !shrubs.some((item) => item.id === values.shrubSpeciesId) &&
      values.shrubSpeciesId !== shrubs[0]?.id
    ) {
      setValue('shrubSpeciesId', shrubs[0]?.id);
    }
  }, [setValue, shortlistLoading, shrubs, values.shrubSpeciesId]);
  const selectedSpecies = availableSpecies.find(
    (item) => item.id === speciesId,
  );
  const compactAlternative = useMemo(() => {
    if (!selectedSpecies) return undefined;
    return availableSpecies
      .filter(
        (item) =>
          item.id !== selectedSpecies.id &&
          (!shortlist ||
            shortlist.some(
              (option) => option.species.id === item.id && option.can_assign,
            )) &&
          item.mature_crown_diameter_max_m <
            selectedSpecies.mature_crown_diameter_max_m,
      )
      .sort(
        (left, right) =>
          left.mature_crown_diameter_max_m - right.mature_crown_diameter_max_m,
      )[0];
  }, [availableSpecies, selectedSpecies, shortlist]);
  const eligible = (id: string | undefined) =>
    Boolean(
      id &&
      (!shortlist ||
        shortlist.some((item) => item.species.id === id && item.can_assign)),
    );
  const validSpecies = Boolean(
    eligible(speciesId) &&
    (values.composition !== 'mixed' || eligible(values.shrubSpeciesId)),
  );

  return {
    catalogSpecies,
    shrubs,
    availableSpecies,
    speciesId,
    primaryField,
    selectedSpecies,
    compactAlternative,
    validSpecies,
  };
}
