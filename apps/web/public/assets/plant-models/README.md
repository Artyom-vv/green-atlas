# Green Atlas plant asset library

This directory contains six ready-made, artist-authored game assets selected
from the [Quaternius Stylized Nature MegaKit](https://quaternius.com/packs/stylizednaturemegakit.html), published
under CC0 1.0. Green Atlas does not generate their tree geometry. The import
only selects supplied models, normalizes them to a ground-centred metre
coordinate system, derives every LOD from the same authored geometry, resizes
textures, and applies meshopt transport compression.

Sources:

- `CommonTree_1.gltf` — rounded broadleaf;
- `CommonTree_3.gltf` — tall oval broadleaf;
- `TwistedTree_3.gltf` — mature spreading broadleaf;
- `Pine_2.gltf` — open pine;
- `Pine_4.gltf` — dense conifer;
- `Bush_Common.gltf` — shrub.

`manifest.json` is the audited runtime contract. It includes every source ID,
author, locked source archive SHA-256, license, selected model, modification list,
species mapping, bounds, LOD thresholds, byte counts, triangle counts, and
SHA-256 output hashes.

The models retain their authored masked foliage and textured trunks. Mid and
far tiers retain complete leaf clusters distributed across the original crown;
their measured extents must remain within 15% of the near silhouette. Runtime
LOD selection uses projected crown size plus hysteresis, so camera distance and
field of view do not cause threshold chatter.

Run `node scripts/import-quaternius-plant-assets.mjs` from `apps/web` to fetch and
rebuild the library. The importer refuses an archive whose SHA-256 differs from
the reviewed value in the script and manifest.

Run `pnpm assets:plants:validate` for the offline shipping gate. Normal app
builds validate the committed artifacts and do not depend on the network.

## Fidelity

These assets are stylized visual morphology archetypes, not surveyed botanical
digital twins. The manifest says so explicitly. Project data remains the source
of truth for mature height and crown diameter, while a catalog species selects
the closest available ready-made visual form.
