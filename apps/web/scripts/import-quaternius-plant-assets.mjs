import { createHash } from 'node:crypto';
import { execFile as execFileCallback } from 'node:child_process';
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import {
  center,
  cloneDocument,
  compactPrimitive,
  dedup,
  flatten,
  getBounds,
  meshopt,
  normals,
  prune,
  simplifyPrimitive,
  textureCompress,
  weld,
} from '@gltf-transform/functions';
import { MeshoptEncoder, MeshoptSimplifier } from 'meshoptimizer';
import sharp from 'sharp';

const execFile = promisify(execFileCallback);
const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const assetDirectory = join(scriptDirectory, '../public/assets/plant-models');
const modelDirectory = join(assetDirectory, 'models');
const cacheDirectory = join(scriptDirectory, '../.cache/plant-assets/quaternius-nature');
const sourceDirectory = join(cacheDirectory, 'source');
const manifestPath = join(assetDirectory, 'manifest.json');

// OpenGameArt hosts the author's Standard archive without an itch.io session.
// The authoritative product page and license remain Quaternius's own page.
const archiveUrl = 'https://opengameart.org/sites/default/files/stylized_nature_megakitstandard.zip';
const archiveSha256 = '298f6732b872e4cf7b30e6e7abf9641c7f6dc6b326df37ac089533ed7e3d58c9';
const assetUrl = 'https://quaternius.com/packs/stylizednaturemegakit.html';
const licenseUrl = 'https://creativecommons.org/publicdomain/zero/1.0/';

const io = new NodeIO()
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.encoder': MeshoptEncoder });

const sources = {
  'broadleaf-round': { file: 'CommonTree_1.gltf', label: 'Rounded broadleaf', height: 11 },
  'broadleaf-oval': { file: 'CommonTree_3.gltf', label: 'Tall oval broadleaf', height: 13 },
  'broadleaf-spreading': { file: 'TwistedTree_3.gltf', label: 'Spreading mature broadleaf', height: 15 },
  'pine-natural': { file: 'Pine_2.gltf', label: 'Open pine', height: 12 },
  'fir-natural': { file: 'Pine_4.gltf', label: 'Dense conifer', height: 14 },
  'shrub-natural': { file: 'Bush_Common.gltf', label: 'Dense shrub', height: 1.8 },
};

const lodPolicy = {
  near: { ratio: 1, error: 0, textureSize: 512, textureQuality: 82 },
  mid: { ratio: 0.58, error: 0.1, textureSize: 256, textureQuality: 74 },
  far: { ratio: 0.22, error: 1, textureSize: 128, textureQuality: 66 },
};

const species = {
  'Tilia cordata': { archetype: 'broadleaf-round', labelRu: 'Липа мелколистная' },
  'Acer platanoides': { archetype: 'broadleaf-oval', labelRu: 'Клён остролистный' },
  'Quercus robur': { archetype: 'broadleaf-spreading', labelRu: 'Дуб черешчатый' },
  'Betula pendula': { archetype: 'broadleaf-oval', labelRu: 'Берёза повислая' },
  'Sorbus aucuparia': { archetype: 'broadleaf-round', labelRu: 'Рябина обыкновенная' },
  'Ulmus laevis': { archetype: 'broadleaf-spreading', labelRu: 'Вяз гладкий' },
  'Pinus sylvestris': { archetype: 'pine-natural', labelRu: 'Сосна обыкновенная' },
  'Picea abies': { archetype: 'fir-natural', labelRu: 'Ель европейская' },
  'Cornus alba': { archetype: 'shrub-natural', labelRu: 'Дерён белый' },
  'Spiraea japonica': { archetype: 'shrub-natural', labelRu: 'Спирея японская' },
};

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function triangleCount(document) {
  return document.getRoot().listMeshes().flatMap((mesh) => mesh.listPrimitives()).reduce((sum, primitive) => {
    const count = primitive.getIndices()?.getCount() ?? primitive.getAttribute('POSITION')?.getCount() ?? 0;
    return sum + Math.floor(count / 3);
  }, 0);
}

function stableScore(value, salt) {
  let result = (value ^ salt) >>> 0;
  result = Math.imul(result ^ (result >>> 16), 0x7feb352d);
  result = Math.imul(result ^ (result >>> 15), 0x846ca68b);
  return (result ^ (result >>> 16)) >>> 0;
}

/**
 * Alpha foliage is built from disconnected authored leaf clusters. Edge
 * collapse cannot reduce a quad/card without destroying it, so retain whole
 * clusters evenly throughout the same crown volume. This is a foliage LOD,
 * not replacement geometry: every surviving triangle and UV is authored.
 */
function thinFoliageComponents(primitive, ratio, salt) {
  const indicesAccessor = primitive.getIndices();
  const positions = primitive.getAttribute('POSITION');
  const indices = indicesAccessor?.getArray();
  if (!indicesAccessor || !positions || !indices || ratio >= 1) return;
  const targetTriangles = Math.max(24, Math.floor(indices.length / 3 * ratio));

  const parent = new Int32Array(positions.getCount());
  const sizes = new Int32Array(positions.getCount()).fill(1);
  for (let index = 0; index < parent.length; index += 1) parent[index] = index;
  const find = (value) => {
    let root = value;
    while (parent[root] !== root) root = parent[root];
    while (parent[value] !== value) {
      const next = parent[value];
      parent[value] = root;
      value = next;
    }
    return root;
  };
  const union = (leftValue, rightValue) => {
    let left = find(leftValue);
    let right = find(rightValue);
    if (left === right) return;
    if (sizes[left] < sizes[right]) [left, right] = [right, left];
    parent[right] = left;
    sizes[left] += sizes[right];
  };
  for (let index = 0; index < indices.length; index += 3) {
    union(indices[index], indices[index + 1]);
    union(indices[index], indices[index + 2]);
  }

  const records = new Map();
  const point = [0, 0, 0];
  for (let index = 0; index < indices.length; index += 3) {
    const root = find(indices[index]);
    let record = records.get(root);
    if (!record) records.set(root, record = { root, triangles: 0, vertices: new Set(), centroid: [0, 0, 0] });
    record.triangles += 1;
    record.vertices.add(indices[index]);
    record.vertices.add(indices[index + 1]);
    record.vertices.add(indices[index + 2]);
  }
  const components = [...records.values()];
  for (const component of components) {
    for (const vertex of component.vertices) {
      positions.getElement(vertex, point);
      for (let axis = 0; axis < 3; axis += 1) component.centroid[axis] += point[axis];
    }
    for (let axis = 0; axis < 3; axis += 1) component.centroid[axis] /= component.vertices.size;
    component.score = stableScore(component.root, salt);
  }
  const minimum = [Infinity, Infinity, Infinity];
  const maximum = [-Infinity, -Infinity, -Infinity];
  for (const component of components) for (let axis = 0; axis < 3; axis += 1) {
    minimum[axis] = Math.min(minimum[axis], component.centroid[axis]);
    maximum[axis] = Math.max(maximum[axis], component.centroid[axis]);
  }
  const cellKey = (component) => component.centroid.map((value, axis) => {
    const span = Math.max(0.0001, maximum[axis] - minimum[axis]);
    return Math.min(4, Math.floor((value - minimum[axis]) / span * 5));
  }).join(':');
  const cells = new Map();
  for (const component of components) {
    const key = cellKey(component);
    const cell = cells.get(key) ?? [];
    cell.push(component);
    cells.set(key, cell);
  }
  for (const cell of cells.values()) cell.sort((left, right) => left.score - right.score);
  const ordered = [];
  const maximumDepth = Math.max(...[...cells.values()].map((cell) => cell.length));
  for (let depth = 0; depth < maximumDepth; depth += 1) {
    const layer = [...cells.values()].map((cell) => cell[depth]).filter(Boolean);
    layer.sort((left, right) => left.score - right.score);
    ordered.push(...layer);
  }
  const selectedRoots = new Set();
  let retainedTriangles = 0;
  // Preserve the six crown extrema before filling the budget. This prevents
  // a reduced tier from visibly shrinking or floating when it appears.
  for (let axis = 0; axis < 3; axis += 1) {
    for (const component of [
      components.reduce((left, right) => left.centroid[axis] <= right.centroid[axis] ? left : right),
      components.reduce((left, right) => left.centroid[axis] >= right.centroid[axis] ? left : right),
    ]) {
      if (selectedRoots.has(component.root)) continue;
      selectedRoots.add(component.root);
      retainedTriangles += component.triangles;
    }
  }
  for (const component of ordered) {
    if (retainedTriangles >= targetTriangles) break;
    if (selectedRoots.has(component.root)) continue;
    selectedRoots.add(component.root);
    retainedTriangles += component.triangles;
  }
  const SelectedIndexArray = indices.constructor;
  const selectedIndices = new SelectedIndexArray(retainedTriangles * 3);
  let writeIndex = 0;
  for (let index = 0; index < indices.length; index += 3) {
    if (!selectedRoots.has(find(indices[index]))) continue;
    selectedIndices[writeIndex++] = indices[index];
    selectedIndices[writeIndex++] = indices[index + 1];
    selectedIndices[writeIndex++] = indices[index + 2];
  }
  primitive.setIndices(indicesAccessor.clone().setArray(selectedIndices));
  compactPrimitive(primitive);
}

async function lockedArchive() {
  const archivePath = join(cacheDirectory, 'stylized-nature-megakit-standard.zip');
  try {
    const cached = await readFile(archivePath);
    if (sha256(cached) === archiveSha256) return { archivePath, bytes: cached };
  } catch {
    // Download the reviewed archive below.
  }
  const response = await fetch(archiveUrl, { headers: { 'User-Agent': 'GreenAtlasAssetPipeline/3.0' } });
  if (!response.ok) throw new Error(`Quaternius archive download failed: ${response.status}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  if (sha256(bytes) !== archiveSha256) throw new Error('Quaternius archive hash changed; review upstream before importing');
  await mkdir(cacheDirectory, { recursive: true });
  await writeFile(archivePath, bytes);
  return { archivePath, bytes };
}

async function extractSources(archivePath) {
  try {
    await execFile('chmod', ['-R', 'u+w', sourceDirectory]);
  } catch {
    // First import has no extracted source directory yet.
  }
  await rm(sourceDirectory, { recursive: true, force: true });
  await mkdir(sourceDirectory, { recursive: true });
  await execFile('unzip', ['-q', '-o', archivePath, 'glTF/*', '-d', sourceDirectory], {
    maxBuffer: 4 * 1024 * 1024,
  });
}

async function normalizedSource(source) {
  const document = await io.read(join(sourceDirectory, 'glTF', source.file));
  for (const material of document.getRoot().listMaterials()) {
    material.setMetallicFactor(0).setRoughnessFactor(Math.max(0.72, material.getRoughnessFactor()));
    if (/leaf|leaves|bush/i.test(material.getName())) {
      // Dense foliage is rendered as a stable masked surface in the planner.
      // A higher cutoff prevents resized/mipmapped half-alpha texels from
      // becoming a pale halo around every leaf cluster.
      material.setAlphaMode('MASK').setAlphaCutoff(0.48).setDoubleSided(true);
    }
  }
  await document.transform(flatten(), weld(), dedup(), center({ pivot: 'below' }), prune());
  const scene = document.getRoot().listScenes()[0];
  const bounds = getBounds(scene);
  const sourceHeight = bounds.max[1] - bounds.min[1];
  if (!(sourceHeight > 0)) throw new Error(`${source.file}: invalid source height`);
  const scale = source.height / sourceHeight;
  for (const child of scene.listChildren()) child.setScale([scale, scale, scale]);
  await document.transform(flatten(), center({ pivot: 'below' }), prune());
  return document;
}

async function buildLod(sourceDocument, archetypeId, lod, settings) {
  const document = cloneDocument(sourceDocument);
  const sourceTriangles = triangleCount(document);
  if (lod === 'far') {
    // At <32 CSS px bark texels are sub-pixel noise. Removing the bark UV
    // seams allows the authored trunk mesh to simplify instead of retaining
    // thousands of vertices solely for an invisible texture layout.
    for (const mesh of document.getRoot().listMeshes()) for (const primitive of mesh.listPrimitives()) {
      const material = primitive.getMaterial();
      if (/leaf|leaves|bush/i.test(material?.getName() ?? '')) continue;
      primitive.setAttribute('TEXCOORD_0', null);
      primitive.setAttribute('NORMAL', null);
      primitive.setAttribute('COLOR_0', null);
      material
        ?.setBaseColorTexture(null)
        .setNormalTexture(null)
        .setBaseColorFactor([0.32, 0.2, 0.12, 1]);
    }
    await document.transform(weld(), prune());
  }
  if (settings.ratio < 1) {
    const primitives = document.getRoot().listMeshes().flatMap((mesh) => mesh.listPrimitives());
    for (const [primitiveIndex, primitive] of primitives.entries()) {
      const triangles = Math.floor((primitive.getIndices()?.getCount() ?? primitive.getAttribute('POSITION')?.getCount() ?? 0) / 3);
      if (triangles < 48) continue;
      if (/leaf|leaves|bush/i.test(primitive.getMaterial()?.getName() ?? '')) {
        thinFoliageComponents(primitive, settings.ratio, stableScore(primitiveIndex, archetypeId.length));
      } else {
        simplifyPrimitive(primitive, {
          simplifier: MeshoptSimplifier,
          ratio: settings.ratio,
          error: settings.error,
          lockBorder: false,
        });
      }
    }
  }
  if (lod === 'far') await document.transform(normals({ overwrite: false }));
  if (lod !== 'near') {
    for (const material of document.getRoot().listMaterials()) material.setNormalTexture(null);
  }
  await document.transform(
    prune(),
    textureCompress({
      encoder: sharp,
      targetFormat: 'webp',
      resize: [settings.textureSize, settings.textureSize],
      quality: settings.textureQuality,
      effort: 6,
    }),
    meshopt({ encoder: MeshoptEncoder, level: 'high' }),
  );
  const buffer = Buffer.from(await io.writeBinary(document));
  const fileName = `${archetypeId}.${lod}.quaternius.glb`;
  await writeFile(join(modelDirectory, fileName), buffer);
  const bounds = getBounds(document.getRoot().listScenes()[0]);
  const triangles = triangleCount(document);
  return {
    lod,
    url: `/assets/plant-models/models/${fileName}`,
    bytes: buffer.length,
    sha256: sha256(buffer),
    triangleCount: triangles,
    sourceTriangleCount: sourceTriangles,
    triangleRetention: Number((triangles / sourceTriangles).toFixed(6)),
    foliageRepresentation: 'artist-authored-alpha-mask',
    materialCount: document.getRoot().listMaterials().length,
    textureCount: document.getRoot().listTextures().length,
    textureMaxSize: settings.textureSize,
    bounds: {
      min: bounds.min.map((value) => Number(value.toFixed(3))),
      max: bounds.max.map((value) => Number(value.toFixed(3))),
    },
  };
}

await mkdir(modelDirectory, { recursive: true });
await mkdir(cacheDirectory, { recursive: true });
const expectedFiles = new Set(
  Object.keys(sources).flatMap((id) => Object.keys(lodPolicy).map((lod) => `${id}.${lod}.quaternius.glb`)),
);
for (const fileName of await readdir(modelDirectory)) {
  if (fileName.endsWith('.glb') && !expectedFiles.has(fileName)) await rm(join(modelDirectory, fileName));
}
await rm(join(assetDirectory, 'basis'), { recursive: true, force: true });

const { archivePath } = await lockedArchive();
await extractSources(archivePath);
const previousManifest = JSON.parse(await readFile(manifestPath, 'utf8'));
const archetypes = {};
let runtimeBytes = 0;
for (const [id, source] of Object.entries(sources)) {
  const sourceDocument = await normalizedSource(source);
  const sourceBounds = getBounds(sourceDocument.getRoot().listScenes()[0]);
  const radius = Math.max(
    Math.abs(sourceBounds.min[0]), Math.abs(sourceBounds.max[0]),
    Math.abs(sourceBounds.min[2]), Math.abs(sourceBounds.max[2]),
  );
  const lods = [];
  for (const [lod, settings] of Object.entries(lodPolicy)) {
    lods.push(await buildLod(sourceDocument, id, lod, settings));
  }
  runtimeBytes += lods.reduce((sum, item) => sum + item.bytes, 0);
  archetypes[id] = {
    id,
    label: source.label,
    nominalHeightM: source.height,
    nominalRadiusM: Number(radius.toFixed(3)),
    nominalCrownDiameterM: Number((radius * 2).toFixed(3)),
    origin: 'ground-center',
    upAxis: '+Y',
    bounds: {
      min: sourceBounds.min.map((value) => Number(value.toFixed(3))),
      max: sourceBounds.max.map((value) => Number(value.toFixed(3))),
    },
    source: {
      provider: 'Quaternius',
      assetId: 'stylized-nature-megakit-standard',
      assetName: 'Stylized Nature MegaKit (Standard)',
      model: source.file,
      assetUrl,
      archiveUrl,
      archiveSha256,
      authors: { Quaternius: 'modeling and textures' },
      license: 'CC0-1.0',
      modifications: [
        'model selection',
        'ground-center normalization',
        'uniform scale',
        'same-source mesh simplification',
        'texture resizing and WebP encoding',
        'meshopt compression',
      ],
      fidelity: 'stylized-volumetric',
    },
    lods,
  };
}

const manifest = {
  version: 2,
  schemaVersion: 2,
  libraryVersion: '3.0.0',
  generator: 'scripts/import-quaternius-plant-assets.mjs',
  license: { spdx: 'CC0-1.0', attributionRequired: false, licenseUrl },
  format: {
    container: 'GLB',
    gltfVersion: '2.0',
    units: 'meter',
    upAxis: '+Y',
    origin: 'ground-center',
    geometryCompression: 'EXT_meshopt_compression',
    textureFallback: 'embedded WebP',
    texturePreferred: 'embedded WebP',
  },
  lodPolicy: {
    selectionMetric: 'projected crown diameter in CSS pixels',
    hysteresisPx: 6,
    tiers: { near: { minDiameterPx: 160 }, mid: { minDiameterPx: 32 }, far: { minDiameterPx: 0 } },
    continuity: 'every tier is simplified from the same authored source mesh and keeps the same pivot',
  },
  ageScaleProfiles: previousManifest.ageScaleProfiles,
  fidelityStatement: 'Six ready-made Quaternius CC0 models preserve authored trunks, textured foliage and distinct crown morphologies. Species mappings are visual approximations, not surveyed botanical twins.',
  speciesMap: Object.fromEntries(Object.entries(species).map(([name, entry]) => [name, entry.archetype])),
  speciesMetadata: Object.fromEntries(Object.entries(species).map(([name, entry]) => [name, {
    archetype: entry.archetype,
    labelRu: entry.labelRu,
    fidelity: 'morphological',
  }])),
  archetypes,
  totals: {
    runtimeBytes,
    ktx2Bytes: 0,
    transcoderBytes: 0,
    initialFallbackBytes: Object.values(archetypes).reduce(
      (sum, item) => sum + item.lods.find((lod) => lod.lod === 'far').bytes,
      0,
    ),
    initialKtx2Bytes: 0,
    libraryBytes: runtimeBytes,
    files: Object.keys(sources).length * 3,
  },
};
await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
console.log(`Imported ${Object.keys(sources).length} Quaternius CC0 archetypes (${(runtimeBytes / 1024 / 1024).toFixed(2)} MiB).`);
