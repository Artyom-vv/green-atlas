# Green Atlas plant asset library

This directory contains lightweight, deterministic plant archetypes for the real-time 3D workspace.

- `manifest.json` is the runtime contract: species mapping, scale, origin, LOD distances, file sizes, triangle counts, and SHA-256 hashes.
- `models/*.glb` are generated glTF 2.0 binary assets. Every archetype has `near`, `mid`, and `far` LODs.
- Run `pnpm assets:plants` from `apps/web` to rebuild the library and `pnpm assets:plants:validate` to validate it.

The geometry is intentionally described as **morphological archetypes**, not botanical scans. Exact catalog species map to a recognizable growth form, while height and crown diameter continue to come from project data. This avoids presenting invented detail as surveyed truth.

The assets use two PBR materials with vertex colors and no textures. That keeps the complete library small, removes alpha-overdraw from foliage cards, and lets the renderer instance each `(archetype, LOD, material)` batch. The runtime should apply a short cross-fade or dithered transition at the manifest thresholds and use its listed hysteresis to avoid LOD flicker.

## Attribution and third-party assets

No third-party geometry or textures are embedded in this generated library. See `LICENSE.txt`. If a scanned or artist-authored near-LOD is added later, its source URL, author, SPDX/Creative Commons identifier, modification history, and attribution text must be added to the manifest before it may ship.
