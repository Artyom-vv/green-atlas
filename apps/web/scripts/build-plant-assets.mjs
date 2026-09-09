import { createHash } from "node:crypto";
import { execFile as execFileCallback } from "node:child_process";
import { copyFile, mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { arch, platform } from "node:process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import {
  center,
  cloneDocument,
  compactPrimitive,
  dedup,
  flatten,
  getBounds,
  meshopt,
  prune,
  simplifyPrimitive,
  textureCompress,
  unpartition,
  weld,
} from "@gltf-transform/functions";
import { MeshoptEncoder, MeshoptSimplifier } from "meshoptimizer";
import sharp from "sharp";

const execFile = promisify(execFileCallback);
const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const webDirectory = join(scriptDirectory, "..");
const outputDirectory = join(webDirectory, "public/assets/plant-models");
const modelDirectory = join(outputDirectory, "models");
const transcoderDirectory = join(outputDirectory, "basis");
const cacheDirectory = join(webDirectory, ".cache/plant-assets");
const sourceDirectory = join(cacheDirectory, "poly-haven");
const toolDirectory = join(cacheDirectory, "ktx-software-4.4.2");
const gltfTransformCli = join(webDirectory, "node_modules/@gltf-transform/cli/bin/cli.js");
const userAgent = "GreenAtlasAssetPipeline/2.0 (+https://polyhaven.com)";

const LODS = {
  // The near tier is a review asset, not merely another performance LOD.
  // Preserve enough texture resolution for individual leaf clusters when an
  // operator focuses a planting at eye level.
  near: { maxDistanceM: 12, textureSize: 512, textureQuality: 82, maxSimplificationError: 0.02 },
  mid: { maxDistanceM: 74, textureSize: 256, textureQuality: 72, maxSimplificationError: 0.02 },
  far: { maxDistanceM: null, textureSize: 128, textureQuality: 64, maxSimplificationError: 1 },
};

// Artist-authored source meshes and PBR textures. Each entry is locked to
// Poly Haven's files hash; the build aborts if upstream content changes until
// the provenance record is deliberately reviewed and updated.
const SOURCES = [
  {
    id: "broadleaf-natural",
    label: "Dense natural broadleaf tree",
    polyHavenId: "tree_small_02",
    polyHavenName: "Tree Small 02",
    filesHash: "5fe4637be7454be81ee92fc68a7938b56d20c613",
    authors: { "Rico Cilliers": "All" },
    sourceNode: "tree_small_02_LOD0",
    opacityMap: { key: "leaves_alpha", materialPattern: /leaves/i },
    nominalHeightM: 11,
    // Preserve enough disconnected leaf clusters for a dense inspection
    // crown. Mid/far use a complete-tree camera-facing bake instead.
    triangleBudget: { near: 245_000, mid: 24_000, far: 1_200 },
  },
  {
    id: "pine-natural",
    label: "Natural pine sapling",
    polyHavenId: "pine_sapling_small",
    polyHavenName: "Pine Sapling Small",
    filesHash: "e756e9c95c4bac208f0074fb7768cc1a1ab09783",
    authors: { "Rob Tuytel": "photography", "Rico Cilliers": "modeling" },
    sourceNode: "pine_sapling_small_a",
    opacityMap: { key: "twig_alpha", materialPattern: /twig/i },
    nominalHeightM: 8,
    triangleBudget: { near: 220_000, mid: 12_000, far: 2_000 },
  },
  {
    id: "fir-natural",
    label: "Natural fir sapling",
    polyHavenId: "fir_sapling_medium",
    polyHavenName: "Fir Sapling Medium",
    filesHash: "0029476c186325a03c000bb1349ec299e43f9331",
    authors: { "Rob Tuytel": "photography", "Rico Cilliers": "modeling" },
    sourceNode: "fir_sapling_medium_a_LOD0",
    opacityMap: { key: "twigs_alpha", materialPattern: /twig/i },
    nominalHeightM: 12,
    triangleBudget: { near: 245_000, mid: 12_000, far: 2_000 },
  },
  {
    id: "shrub-natural",
    label: "Natural woodland shrub",
    polyHavenId: "shrub_02",
    polyHavenName: "Shrub 02",
    filesHash: "b8a0252030e27a0d209db5e564cd6aa62e2bb9db",
    authors: { "Rico Cilliers": "All" },
    sourceNode: "shrub_02_a",
    opacityMap: { key: "Alpha", materialPattern: /shrub/i },
    nominalHeightM: 1.8,
    triangleBudget: { near: 8_000, mid: 3_000, far: 700 },
  },
];

const SPECIES = {
  "Tilia cordata": { archetype: "broadleaf-natural", labelRu: "Липа мелколистная" },
  "Acer platanoides": { archetype: "broadleaf-natural", labelRu: "Клён остролистный" },
  "Quercus robur": { archetype: "broadleaf-natural", labelRu: "Дуб черешчатый" },
  "Betula pendula": { archetype: "broadleaf-natural", labelRu: "Берёза повислая" },
  "Sorbus aucuparia": { archetype: "broadleaf-natural", labelRu: "Рябина обыкновенная" },
  "Ulmus laevis": { archetype: "broadleaf-natural", labelRu: "Вяз гладкий" },
  "Pinus sylvestris": { archetype: "pine-natural", labelRu: "Сосна обыкновенная" },
  "Picea abies": { archetype: "fir-natural", labelRu: "Ель европейская" },
  "Cornus alba": { archetype: "shrub-natural", labelRu: "Дерён белый" },
  "Spiraea japonica": { archetype: "shrub-natural", labelRu: "Спирея японская" },
};

const KTX_TOOLS = {
  "darwin-arm64": {
    url: "https://github.com/KhronosGroup/KTX-Software/releases/download/v4.4.2/KTX-Software-4.4.2-Darwin-arm64.pkg",
    sha256: "500bd8f9d63358c3f3a0d83b724c8574436a72c37dc0e4bad90ec1ca38032c3c",
    archive: "pkg",
  },
  "darwin-x64": {
    url: "https://github.com/KhronosGroup/KTX-Software/releases/download/v4.4.2/KTX-Software-4.4.2-Darwin-x86_64.pkg",
    sha256: "efecc685ab891a6e119a9fdc8cbe038e135f9a367eb2f5d8a059553f947f1fea",
    archive: "pkg",
  },
  "linux-arm64": {
    url: "https://github.com/KhronosGroup/KTX-Software/releases/download/v4.4.2/KTX-Software-4.4.2-Linux-arm64.tar.bz2",
    sha256: "60382e7b842177b8048bd58ccdc770383f8ef65b94452a25d3afdb55f2405c5a",
    archive: "tar.bz2",
  },
  "linux-x64": {
    url: "https://github.com/KhronosGroup/KTX-Software/releases/download/v4.4.2/KTX-Software-4.4.2-Linux-x86_64.tar.bz2",
    sha256: "a8781bad05f9624edbf910b7f258cd0a4ba7d3e63b49ecc0a0ab440bf6a0a245",
    archive: "tar.bz2",
  },
};

const io = new NodeIO()
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ "meshopt.encoder": MeshoptEncoder });

function hash(buffer, algorithm = "sha256") {
  return createHash(algorithm).update(buffer).digest("hex");
}

async function download(url, destination, expectedHash, algorithm = "md5") {
  try {
    const existing = await readFile(destination);
    if (hash(existing, algorithm) === expectedHash) return existing;
  } catch {
    // Cache miss; fetch the locked source below.
  }
  const response = await fetch(url, { headers: { "User-Agent": userAgent } });
  if (!response.ok) throw new Error(`Download failed (${response.status}): ${url}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  if (hash(bytes, algorithm) !== expectedHash) throw new Error(`Checksum mismatch: ${url}`);
  await mkdir(dirname(destination), { recursive: true });
  await writeFile(destination, bytes);
  return bytes;
}

async function fetchJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json", "User-Agent": userAgent } });
  if (!response.ok) throw new Error(`Request failed (${response.status}): ${url}`);
  return response.json();
}

async function fetchSource(source) {
  const info = await fetchJson(`https://api.polyhaven.com/info/${source.polyHavenId}`);
  if (info.files_hash !== source.filesHash) {
    throw new Error(`${source.polyHavenId}: upstream files changed (${info.files_hash}); review before updating the lock`);
  }
  const files = await fetchJson(`https://api.polyhaven.com/files/${source.polyHavenId}`);
  const gltf = files.gltf?.["1k"]?.gltf;
  if (!gltf?.url || !gltf?.md5 || !gltf?.include) throw new Error(`${source.polyHavenId}: missing locked 1K glTF package`);
  const assetDirectory = join(sourceDirectory, source.polyHavenId);
  const gltfPath = join(assetDirectory, basename(new URL(gltf.url).pathname));
  await download(gltf.url, gltfPath, gltf.md5);
  for (const [path, file] of Object.entries(gltf.include)) {
    await download(file.url, join(assetDirectory, path), file.md5);
  }
  const opacityFile = files[source.opacityMap.key]?.["1k"]?.png;
  if (!opacityFile?.url || !opacityFile?.md5) throw new Error(`${source.polyHavenId}: missing reviewed opacity map`);
  const opacityPath = join(assetDirectory, "textures", basename(new URL(opacityFile.url).pathname));
  await download(opacityFile.url, opacityPath, opacityFile.md5);
  return { gltfPath, opacityPath };
}

async function applyOpacityMap(document, source, opacityPath) {
  const opacityImage = await readFile(opacityPath);
  for (const material of document.getRoot().listMaterials()) {
    if (!source.opacityMap.materialPattern.test(material.getName())) continue;
    const texture = material.getBaseColorTexture();
    const baseImage = texture?.getImage();
    if (!texture || !baseImage) throw new Error(`${source.id}: cutout material has no base-colour image`);
    const base = await sharp(baseImage).removeAlpha().raw().toBuffer({ resolveWithObject: true });
    const alpha = await sharp(opacityImage)
      .resize(base.info.width, base.info.height, { fit: "fill" })
      .greyscale()
      .raw()
      .toBuffer();
    const rgba = Buffer.alloc(base.info.width * base.info.height * 4);
    for (let pixel = 0; pixel < base.info.width * base.info.height; pixel += 1) {
      rgba[pixel * 4] = base.data[pixel * base.info.channels];
      rgba[pixel * 4 + 1] = base.data[pixel * base.info.channels + 1];
      rgba[pixel * 4 + 2] = base.data[pixel * base.info.channels + 2];
      rgba[pixel * 4 + 3] = alpha[pixel];
    }
    const encoded = await sharp(rgba, {
      raw: { width: base.info.width, height: base.info.height, channels: 4 },
    }).png({ compressionLevel: 9 }).toBuffer();
    texture.setImage(encoded).setMimeType("image/png");
    material.setAlphaMode("MASK").setAlphaCutoff(0.28).setDoubleSided(true);
  }
}

function directSceneNode(document, name) {
  const scene = document.getRoot().listScenes()[0];
  const selected = scene.listChildren().find((node) => node.getName() === name);
  if (!selected) throw new Error(`Source node not found: ${name}`);
  for (const child of scene.listChildren()) {
    if (child !== selected) child.dispose();
  }
  return scene;
}

function triangleCount(document) {
  return document.getRoot().listMeshes().reduce((total, mesh) => total + mesh.listPrimitives().reduce((meshTotal, primitive) => {
    const indices = primitive.getIndices();
    const vertexCount = indices?.getCount() ?? primitive.getAttribute("POSITION")?.getCount() ?? 0;
    return meshTotal + Math.floor(vertexCount / 3);
  }, 0), 0);
}

function primitiveTriangleCount(primitive) {
  const indices = primitive.getIndices();
  const vertexCount = indices?.getCount() ?? primitive.getAttribute("POSITION")?.getCount() ?? 0;
  return Math.floor(vertexCount / 3);
}

function componentScore(value, salt) {
  let result = (value ^ salt) >>> 0;
  result = Math.imul(result ^ (result >>> 16), 0x7feb352d);
  result = Math.imul(result ^ (result >>> 15), 0x846ca68b);
  return (result ^ (result >>> 16)) >>> 0;
}

// Foliage consists of tens of thousands of disconnected, artist-authored leaf
// meshes. Generic edge-collapse joins their projected silhouette into large
// vertical sheets — the visible "cage" regression. A foliage LOD must retain
// complete leaves and remove whole disconnected components instead.
function thinFoliageComponents(primitive, targetTriangles, salt) {
  const indicesAccessor = primitive.getIndices();
  const positions = primitive.getAttribute("POSITION");
  const indices = indicesAccessor?.getArray();
  if (!indicesAccessor || !positions || !indices) return { occupancy: 1, projectedDensity: 1, scale: 1 };
  if (targetTriangles >= Math.floor(indices.length / 3)) return { occupancy: 1, projectedDensity: 1, scale: 1 };

  const parent = new Int32Array(positions.getCount());
  const size = new Int32Array(positions.getCount());
  for (let index = 0; index < parent.length; index += 1) {
    parent[index] = index;
    size[index] = 1;
  }
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
    if (size[left] < size[right]) [left, right] = [right, left];
    parent[right] = left;
    size[left] += size[right];
  };
  for (let index = 0; index < indices.length; index += 3) {
    union(indices[index], indices[index + 1]);
    union(indices[index], indices[index + 2]);
  }

  const triangleCounts = new Map();
  const componentVertices = new Map();
  for (let index = 0; index < indices.length; index += 3) {
    const root = find(indices[index]);
    triangleCounts.set(root, (triangleCounts.get(root) ?? 0) + 1);
    let vertices = componentVertices.get(root);
    if (!vertices) componentVertices.set(root, vertices = new Set());
    vertices.add(indices[index]);
    vertices.add(indices[index + 1]);
    vertices.add(indices[index + 2]);
  }
  const components = [...triangleCounts.entries()].map(([root, triangles]) => {
    const vertices = componentVertices.get(root);
    const centroid = [0, 0, 0];
    const point = [0, 0, 0];
    for (const vertex of vertices) {
      positions.getElement(vertex, point);
      centroid[0] += point[0];
      centroid[1] += point[1];
      centroid[2] += point[2];
    }
    centroid[0] /= vertices.size;
    centroid[1] /= vertices.size;
    centroid[2] /= vertices.size;
    return { root, triangles, vertices, centroid, score: componentScore(root, salt) };
  });

  // A global random sample can satisfy a triangle budget while accidentally
  // removing whole crown lobes. Bucket component centroids in 3D and retain a
  // representative from every occupied cell before spending the remaining
  // budget. This preserves the authored crown volume at every deterministic
  // rebuild without trying to simplify alpha cards into opaque sheets.
  const boundsMin = [Infinity, Infinity, Infinity];
  const boundsMax = [-Infinity, -Infinity, -Infinity];
  for (const component of components) {
    for (let axis = 0; axis < 3; axis += 1) {
      boundsMin[axis] = Math.min(boundsMin[axis], component.centroid[axis]);
      boundsMax[axis] = Math.max(boundsMax[axis], component.centroid[axis]);
    }
  }
  const cellKey = (component) => component.centroid.map((value, axis) => {
    const span = Math.max(0.0001, boundsMax[axis] - boundsMin[axis]);
    return Math.min(5, Math.floor((value - boundsMin[axis]) / span * 6));
  }).join(":");
  const cells = new Map();
  for (const component of components) {
    const key = cellKey(component);
    const entries = cells.get(key) ?? [];
    entries.push(component);
    cells.set(key, entries);
  }
  for (const entries of cells.values()) entries.sort((left, right) => left.score - right.score);
  const ordered = [];
  const maxCellDepth = Math.max(...[...cells.values()].map((entries) => entries.length));
  for (let depth = 0; depth < maxCellDepth; depth += 1) {
    const layer = [...cells.values()].map((entries) => entries[depth]).filter(Boolean);
    layer.sort((left, right) => left.score - right.score);
    ordered.push(...layer);
  }
  const selected = new Set();
  let retainedTriangles = 0;
  for (const component of ordered) {
    if (retainedTriangles >= targetTriangles) break;
    selected.add(component.root);
    retainedTriangles += component.triangles;
  }

  // Compensate alpha-covered area, conservatively capped so leaves do not
  // become obvious giant cards. The scale is local to each disconnected leaf
  // cluster and does not change the overall crown placement or trunk.
  const maximumCoverageScale = /(?:pine|fir)-natural/.test(String(salt)) ? 3.35 : 2.3;
  const coverageScale = Math.min(maximumCoverageScale, Math.sqrt(indices.length / 3 / Math.max(1, retainedTriangles)) * 0.62 + 0.38);
  if (coverageScale > 1.001) {
    const point = [0, 0, 0];
    for (const component of components) {
      if (!selected.has(component.root)) continue;
      for (const vertex of component.vertices) {
        positions.getElement(vertex, point);
        positions.setElement(vertex, [
          component.centroid[0] + (point[0] - component.centroid[0]) * coverageScale,
          component.centroid[1] + (point[1] - component.centroid[1]) * coverageScale,
          component.centroid[2] + (point[2] - component.centroid[2]) * coverageScale,
        ]);
      }
    }
  }

  const SelectedIndexArray = indices.constructor;
  const selectedIndices = new SelectedIndexArray(retainedTriangles * 3);
  let writeIndex = 0;
  for (let index = 0; index < indices.length; index += 3) {
    if (!selected.has(find(indices[index]))) continue;
    selectedIndices[writeIndex++] = indices[index];
    selectedIndices[writeIndex++] = indices[index + 1];
    selectedIndices[writeIndex++] = indices[index + 2];
  }
  primitive.setIndices(indicesAccessor.clone().setArray(selectedIndices));
  compactPrimitive(primitive);
  const occupiedSelectedCells = new Set(components.filter((component) => selected.has(component.root)).map(cellKey)).size;
  const projectedDensity = [[0, 1], [2, 1]].reduce((minimum, [horizontalAxis, verticalAxis]) => {
    const sourceCells = new Map();
    const selectedCells = new Map();
    const projectedKey = (component) => [horizontalAxis, verticalAxis].map((axis) => {
      const span = Math.max(0.0001, boundsMax[axis] - boundsMin[axis]);
      return Math.min(15, Math.floor((component.centroid[axis] - boundsMin[axis]) / span * 16));
    }).join(":");
    for (const component of components) {
      const key = projectedKey(component);
      sourceCells.set(key, (sourceCells.get(key) ?? 0) + component.triangles);
      if (selected.has(component.root)) selectedCells.set(key, (selectedCells.get(key) ?? 0) + component.triangles * coverageScale ** 2);
    }
    let sourceArea = 0;
    let retainedArea = 0;
    for (const [key, area] of sourceCells) {
      sourceArea += area;
      retainedArea += Math.min(area, selectedCells.get(key) ?? 0);
    }
    return Math.min(minimum, retainedArea / Math.max(1, sourceArea));
  }, 1);
  return {
    occupancy: occupiedSelectedCells / Math.max(1, cells.size),
    projectedDensity,
    scale: coverageScale,
  };
}

function tuneMaterials(document) {
  for (const material of document.getRoot().listMaterials()) {
    const name = material.getName().toLowerCase();
    const isFoliage = /(leaf|leaves|twig|shrub|foliage)/.test(name);
    material
      .setMetallicFactor(0)
      .setRoughnessFactor(Math.max(0.58, material.getRoughnessFactor()))
      .setDoubleSided(isFoliage || material.getDoubleSided());
    if (isFoliage && material.getAlphaMode() !== "OPAQUE") {
      const [red, green, blue, alpha] = material.getBaseColorFactor();
      material
        // Keep the photographed albedo, but apply a conservative foliage
        // energy/tint factor. Pure-white factors made thin two-sided cards
        // read as pale twigs under the high-key outdoor rig.
        .setBaseColorFactor([red * 0.58, green * 0.78, blue * 0.48, alpha])
        .setAlphaMode("MASK")
        .setAlphaCutoff(0.28);
    }
  }
}

async function replaceFoliageWithCrossCard(document, foliage, textureSize, includeDiagonal, clustered = false) {
  if (!foliage.length) return { occupancy: 0, projectedDensity: 0, scale: 1 };
  const positions = foliage.flatMap((primitive) => {
    const accessor = primitive.getAttribute("POSITION");
    if (!accessor) return [];
    const point = [0, 0, 0];
    const result = [];
    const stride = Math.max(1, Math.floor(accessor.getCount() / 420));
    for (let index = 0; index < accessor.getCount(); index += stride) {
      accessor.getElement(index, point);
      result.push([...point]);
    }
    return result;
  });
  if (!positions.length) return { occupancy: 0, projectedDensity: 0, scale: 1 };
  const boundsMin = [Infinity, Infinity, Infinity];
  const boundsMax = [-Infinity, -Infinity, -Infinity];
  for (const point of positions) for (let axis = 0; axis < 3; axis += 1) {
    boundsMin[axis] = Math.min(boundsMin[axis], point[axis]);
    boundsMax[axis] = Math.max(boundsMax[axis], point[axis]);
  }
  const sourceMaterial = foliage[0].getMaterial();
  const sourceImage = sourceMaterial?.getBaseColorTexture()?.getImage();
  if (!sourceMaterial || !sourceImage) return { occupancy: 0, projectedDensity: 0, scale: 1 };
  const canvasSize = Math.max(128, textureSize);
  const stampSize = Math.max(26, Math.round(canvasSize * 0.23));
  const stamp = await sharp(sourceImage).resize(stampSize, stampSize, { fit: "inside" }).png().toBuffer();
  const composites = positions.slice(0, 420).map((point) => ({
    input: stamp,
    left: Math.max(0, Math.min(canvasSize - stampSize, Math.round((point[0] - boundsMin[0]) / Math.max(0.001, boundsMax[0] - boundsMin[0]) * (canvasSize - stampSize)))),
    top: Math.max(0, Math.min(canvasSize - stampSize, Math.round((1 - (point[1] - boundsMin[1]) / Math.max(0.001, boundsMax[1] - boundsMin[1])) * (canvasSize - stampSize)))),
    blend: "over",
  }));
  const image = await sharp({
    create: { width: canvasSize, height: canvasSize, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } },
  }).composite(composites).png({ compressionLevel: 9 }).toBuffer();
  const bakedPixels = await sharp(image).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  let coveredPixels = 0;
  for (let index = 3; index < bakedPixels.data.length; index += bakedPixels.info.channels) {
    if (bakedPixels.data[index] >= 51) coveredPixels += 1;
  }
  const projectedDensity = coveredPixels / Math.max(1, bakedPixels.info.width * bakedPixels.info.height);
  const texture = document.createTexture("source-derived foliage impostor").setImage(image).setMimeType("image/png");
  const material = document.createMaterial("source-derived foliage impostor")
    .setBaseColorTexture(texture)
    .setBaseColorFactor(sourceMaterial.getBaseColorFactor())
    .setMetallicFactor(0)
    .setRoughnessFactor(Math.max(0.7, sourceMaterial.getRoughnessFactor()))
    .setAlphaMode("MASK")
    .setAlphaCutoff(0.2)
    .setDoubleSided(true);
  const x0 = boundsMin[0], x1 = boundsMax[0], y0 = boundsMin[1], y1 = boundsMax[1];
  const z0 = boundsMin[2], z1 = boundsMax[2], xc = (x0 + x1) / 2, zc = (z0 + z1) / 2;
  const cardPositions = [], cardNormals = [], cardUvs = [], cardIndices = [];
  const addPlane = (corners, normal) => {
    const base = cardPositions.length / 3;
    cardPositions.push(...corners);
    for (let index = 0; index < 4; index += 1) cardNormals.push(...normal);
    cardUvs.push(0,1, 1,1, 1,0, 0,0);
    cardIndices.push(base,base+1,base+2, base,base+2,base+3);
  };
  if (clustered) {
    const cells = new Map();
    for (const point of positions) {
      const key = point.map((value, axis) => {
        const count = axis === 1 ? 7 : 5;
        return Math.min(count - 1, Math.floor((value - boundsMin[axis]) / Math.max(0.001, boundsMax[axis] - boundsMin[axis]) * count));
      }).join(":");
      const cell = cells.get(key) ?? { sum: [0,0,0], count: 0 };
      for (let axis = 0; axis < 3; axis += 1) cell.sum[axis] += point[axis];
      cell.count += 1;
      cells.set(key, cell);
    }
    const halfX = (x1 - x0) / 5 * 0.72, halfZ = (z1 - z0) / 5 * 0.72, halfY = (y1 - y0) / 7 * 0.72;
    for (const cell of cells.values()) {
      const [cx,cy,cz] = cell.sum.map((value) => value / cell.count);
      const left = Math.max(x0, cx - halfX), right = Math.min(x1, cx + halfX);
      const bottom = Math.max(y0, cy - halfY), top = Math.min(y1, cy + halfY);
      const back = Math.max(z0, cz - halfZ), front = Math.min(z1, cz + halfZ);
      addPlane([left,bottom,cz, right,bottom,cz, right,top,cz, left,top,cz], [0,0,1]);
      addPlane([cx,bottom,back, cx,bottom,front, cx,top,front, cx,top,back], [1,0,0]);
    }
  } else {
    // Runtime rotates this single card to the camera. Extra crossed cards
    // become dark star-shaped artefacts from an oblique/top plan camera and
    // spend fill-rate without adding silhouette information.
    addPlane([x0,y0,zc, x1,y0,zc, x1,y1,zc, x0,y1,zc], [0,0,1]);
  }
  const primitive = document.createPrimitive()
    .setAttribute("POSITION", document.createAccessor().setType("VEC3").setArray(new Float32Array(cardPositions)))
    .setAttribute("NORMAL", document.createAccessor().setType("VEC3").setArray(new Float32Array(cardNormals)))
    .setAttribute("TEXCOORD_0", document.createAccessor().setType("VEC2").setArray(new Float32Array(cardUvs)))
    .setIndices(document.createAccessor().setType("SCALAR").setArray(new Uint16Array(cardIndices)))
    .setMaterial(material);
  document.getRoot().listScenes()[0].addChild(document.createNode("source-derived foliage impostor")
    .setMesh(document.createMesh("source-derived foliage impostor").addPrimitive(primitive)));
  foliage.forEach((item) => item.dispose());
  return { occupancy: 1, projectedDensity, scale: 1 };
}

async function addWholeTreeImpostors(document, source, textureSize, angles) {
  const scene = document.getRoot().listScenes()[0];
  const bounds = getBounds(scene);
  const centerX = (bounds.min[0] + bounds.max[0]) / 2;
  const centerZ = (bounds.min[2] + bounds.max[2]) / 2;
  const width = Math.max(bounds.max[0] - bounds.min[0], bounds.max[2] - bounds.min[2]);
  const height = bounds.max[1] - bounds.min[1];
  const halfWidth = width / 2;
  const mesh = document.createMesh("authored whole-tree impostors");
  let minimumDensity = 1;
  let minimumLuminance = 255;
  let minimumGreen = 255;
  for (const angle of angles) {
    const bakedPath = join(scriptDirectory, `assets/plant-impostors/${source.id}/view-${angle}.png`);
    // The Blender bake is square for review consistency, but a tall conifer
    // may occupy only a narrow strip of it. Crop transparent padding before
    // mapping the image to the physical source bounds; otherwise the padding
    // is scaled as if it were part of the crown and the emitted card becomes
    // an artificial black/stringy column.
    let bakedPipeline = sharp(await readFile(bakedPath))
      .trim({ background: { r: 0, g: 0, b: 0, alpha: 0 }, threshold: 4 })
      .resize(textureSize, textureSize, { fit: "fill" });
    if (source.id === "pine-natural" || source.id === "fir-natural") {
      // Prepared conifer bakes contain physically plausible but extremely dark
      // needle albedo. On an unlit operational billboard that reads as black;
      // lift RGB only, preserving the reviewed cutout alpha byte-for-byte.
      bakedPipeline = bakedPipeline.linear([3.1, 3.1, 3.1, 1], [0, 0, 0, 0]);
    }
    const baked = await bakedPipeline
      .png({ compressionLevel: 9 })
      .toBuffer();
    const pixels = await sharp(baked).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
    let covered = 0;
    let red = 0, green = 0, blue = 0;
    for (let index = 0; index < pixels.data.length; index += pixels.info.channels) {
      if (pixels.data[index + 3] < 51) continue;
      covered += 1;
      red += pixels.data[index]; green += pixels.data[index + 1]; blue += pixels.data[index + 2];
    }
    minimumLuminance = Math.min(minimumLuminance, (0.2126 * red + 0.7152 * green + 0.0722 * blue) / Math.max(1, covered));
    minimumGreen = Math.min(minimumGreen, green / Math.max(1, covered));
    minimumDensity = Math.min(minimumDensity, covered / (pixels.info.width * pixels.info.height));
    const texture = document.createTexture(`whole-tree ${angle}deg`).setImage(baked).setMimeType("image/png");
    const material = document.createMaterial(`whole-tree ${angle}deg`)
      .setBaseColorTexture(texture)
      .setBaseColorFactor([0.76, 0.88, 0.72, 1])
      .setMetallicFactor(0)
      .setRoughnessFactor(1)
      .setAlphaMode("MASK")
      .setAlphaCutoff(0.2)
      .setDoubleSided(true);
    const radians = angle * Math.PI / 180;
    const dx = Math.cos(radians) * halfWidth;
    const dz = Math.sin(radians) * halfWidth;
    const nx = -Math.sin(radians);
    const nz = Math.cos(radians);
    const positions = new Float32Array([
      centerX-dx,bounds.min[1],centerZ-dz, centerX+dx,bounds.min[1],centerZ+dz,
      centerX+dx,bounds.min[1]+height,centerZ+dz, centerX-dx,bounds.min[1]+height,centerZ-dz,
    ]);
    mesh.addPrimitive(document.createPrimitive()
      .setAttribute("POSITION", document.createAccessor().setType("VEC3").setArray(positions))
      .setAttribute("NORMAL", document.createAccessor().setType("VEC3").setArray(new Float32Array([nx,0,nz, nx,0,nz, nx,0,nz, nx,0,nz])))
      .setAttribute("TEXCOORD_0", document.createAccessor().setType("VEC2").setArray(new Float32Array([0,1, 1,1, 1,0, 0,0])))
      .setIndices(document.createAccessor().setType("SCALAR").setArray(new Uint16Array([0,1,2, 0,2,3])))
      .setMaterial(material));
  }
  scene.addChild(document.createNode("authored whole-tree impostors").setMesh(mesh));
  return { occupancy: 1, projectedDensity: minimumDensity, scale: 1, luminance: minimumLuminance, green: minimumGreen };
}

async function prepareSource(source) {
  const { gltfPath, opacityPath } = await fetchSource(source);
  const document = await io.read(gltfPath);
  const scene = directSceneNode(document, source.sourceNode);
  await applyOpacityMap(document, source, opacityPath);
  await document.transform(flatten(), prune(), dedup(), weld(), center({ pivot: "below" }));
  const bounds = getBounds(scene);
  const sourceHeight = bounds.max[1] - bounds.min[1];
  if (!(sourceHeight > 0)) throw new Error(`${source.id}: invalid source bounds`);
  const scale = source.nominalHeightM / sourceHeight;
  for (const child of scene.listChildren()) child.setScale([scale, scale, scale]);
  await document.transform(flatten(), center({ pivot: "below" }), prune(), unpartition());
  tuneMaterials(document);
  return document;
}

async function bakePreparedImpostors(source, prepared) {
  if (source.id === "shrub-natural") return;
  const preparedPath = join(cacheDirectory, `${source.id}.prepared-for-impostor.glb`);
  await writeFile(preparedPath, Buffer.from(await io.writeBinary(prepared)));
  if (process.env.PLANT_BAKE_IMPOSTORS !== "1") return;
  const blenderBinary = process.env.BLENDER_BIN
    ?? (platform === "darwin" ? "/Applications/Blender.app/Contents/MacOS/Blender" : "blender");
  const output = join(scriptDirectory, `assets/plant-impostors/${source.id}`);
  await execFile(blenderBinary, [
    "--background",
    "--python", join(scriptDirectory, "blender-bake-plant-impostors.py"),
    "--", preparedPath, "-", output,
  ], { maxBuffer: 16 * 1024 * 1024 });
}

async function buildLod(source, prepared, lodName) {
  const lod = LODS[lodName];
  const document = cloneDocument(prepared);
  const primitives = document.getRoot().listMeshes().flatMap((mesh) => mesh.listPrimitives());
  const sourceTriangles = primitives.reduce((sum, primitive) => sum + primitiveTriangleCount(primitive), 0);
  const target = Math.min(sourceTriangles, source.triangleBudget[lodName]);
  const foliageMetrics = [];
  if (target < sourceTriangles) {
    const foliage = primitives.filter((primitive) => primitive.getMaterial()?.getAlphaMode() === "MASK");
    const wood = primitives.filter((primitive) => primitive.getMaterial()?.getAlphaMode() !== "MASK");
    const protectedWood = new Set(lodName === "near"
      ? wood.filter((primitive) => /(trunk|bark)/i.test(primitive.getMaterial()?.getName() ?? ""))
      : []);
    const foliageSource = foliage.reduce((sum, primitive) => sum + primitiveTriangleCount(primitive), 0);
    const woodSource = wood.reduce((sum, primitive) => sum + primitiveTriangleCount(primitive), 0);
    const protectedWoodSource = [...protectedWood].reduce((sum, primitive) => sum + primitiveTriangleCount(primitive), 0);
    const simplifiableWoodSource = Math.max(0, woodSource - protectedWoodSource);
    const simplifiableWoodBudget = Math.min(
      simplifiableWoodSource,
      Math.max(0, Math.round(target * (lodName === "near" ? 0.03 : 0.28))),
    );
    const woodBudget = protectedWoodSource + simplifiableWoodBudget;
    const foliageBudget = Math.min(foliageSource, Math.max(1, target - woodBudget));
    if (source.id === "shrub-natural") {
      foliageMetrics.push(await replaceFoliageWithCrossCard(document, foliage, lod.textureSize, lodName === "mid"));
    } else {
      if (lodName === "near") {
        for (const [index, primitive] of foliage.entries()) {
          const primitiveTriangles = primitiveTriangleCount(primitive);
          foliageMetrics.push(thinFoliageComponents(
            primitive,
            Math.max(12, Math.round(foliageBudget * primitiveTriangles / Math.max(1, foliageSource))),
            `${source.id}:${lodName}:${index}`,
          ));
        }
      } else {
        foliage.forEach((primitive) => primitive.dispose());
        // One complete-tree view is enough: the runtime treats it as a
        // spherical billboard. Keeping the perpendicular duplicate made the
        // pair visible edge-on as a cross from the default aerial camera.
        foliageMetrics.push(await addWholeTreeImpostors(document, source, lod.textureSize, [0]));
      }
    }
    wood.forEach((primitive) => {
      // The shrub impostor already represents the complete leafy mass. Its
      // simplified internal twigs projected beyond the card from aerial
      // views and read as a dark star, so only the authored near tier keeps
      // physical wood geometry.
      if (source.id === "shrub-natural" && lodName !== "near") {
        primitive.dispose();
        return;
      }
      if (protectedWood.has(primitive)) return;
      const primitiveTriangles = primitiveTriangleCount(primitive);
      const primitiveTarget = Math.max(
        12,
        Math.round(simplifiableWoodBudget * primitiveTriangles / Math.max(1, simplifiableWoodSource)),
      );
      if (primitiveTarget >= primitiveTriangles) return;
      simplifyPrimitive(primitive, {
        simplifier: MeshoptSimplifier,
        ratio: Math.max(0.001, primitiveTarget / primitiveTriangles),
        error: lod.maxSimplificationError,
        lockBorder: false,
      });
    });
  }
  await document.transform(prune());
  // Component pruning and source hierarchy flattening can slightly change the
  // effective bounds. Re-normalize every emitted LOD so switching species or
  // LOD never changes the real-world height contract.
  const outputScene = document.getRoot().listScenes()[0];
  const outputBounds = getBounds(outputScene);
  const outputHeight = outputBounds.max[1] - outputBounds.min[1];
  if (!(outputHeight > 0)) throw new Error(`${source.id}/${lodName}: invalid output bounds`);
  const outputScale = source.nominalHeightM / outputHeight;
  for (const child of outputScene.listChildren()) {
    const currentScale = child.getScale();
    child.setScale(currentScale.map((value) => value * outputScale));
  }
  await document.transform(flatten(), center({ pivot: "below" }), prune());
  // Preserve the source image format for the KTX2 input. In particular,
  // foliage base-colour maps carry alpha in PNG; an intermediate JPEG would
  // turn every leaf card into an opaque rectangle before either output branch.
  const ktxDocument = cloneDocument(document);
  await ktxDocument.transform(textureCompress({
    encoder: sharp,
    resize: [lod.textureSize, lod.textureSize],
    quality: lod.textureQuality,
    effort: 6,
  }));
  const ktxSourcePath = join(cacheDirectory, `${source.id}.${lodName}.source.glb`);
  await writeFile(ktxSourcePath, Buffer.from(await io.writeBinary(ktxDocument)));
  await document.transform(
    textureCompress({
      encoder: sharp,
      targetFormat: "webp",
      resize: [lod.textureSize, lod.textureSize],
      quality: lod.textureQuality,
      effort: 6,
    }),
    meshopt({ encoder: MeshoptEncoder, level: "high" }),
  );
  const outputPath = join(modelDirectory, `${source.id}.${lodName}.glb`);
  const buffer = Buffer.from(await io.writeBinary(document));
  await writeFile(outputPath, buffer);
  const bounds = getBounds(document.getRoot().listScenes()[0]);
  const radius = Math.max(Math.abs(bounds.min[0]), Math.abs(bounds.max[0]), Math.abs(bounds.min[2]), Math.abs(bounds.max[2]));
  return {
    lod: lodName,
    url: `/assets/plant-models/models/${basename(outputPath)}`,
    bytes: buffer.length,
    sha256: hash(buffer),
    triangleCount: triangleCount(document),
    sourceTriangleCount: sourceTriangles,
    triangleRetention: Number((triangleCount(document) / Math.max(1, sourceTriangles)).toFixed(6)),
    canopyOccupancy: Number((foliageMetrics.length
      ? Math.min(...foliageMetrics.map((metric) => metric.occupancy))
      : 1).toFixed(6)),
    projectedCanopyDensity: Number((foliageMetrics.length
      ? Math.min(...foliageMetrics.map((metric) => metric.projectedDensity))
      : 1).toFixed(6)),
    foliageCoverageScale: Number((foliageMetrics.length
      ? Math.max(...foliageMetrics.map((metric) => metric.scale))
      : 1).toFixed(6)),
    foliageLuminance: Number((foliageMetrics.length
      ? Math.min(...foliageMetrics.map((metric) => metric.luminance ?? 255))
      : 255).toFixed(3)),
    foliageGreen: Number((foliageMetrics.length
      ? Math.min(...foliageMetrics.map((metric) => metric.green ?? 255))
      : 255).toFixed(3)),
    foliageRepresentation: lodName === "near" ? "authored-geometry" : "camera-facing-billboard",
    materialCount: document.getRoot().listMaterials().length,
    textureCount: document.getRoot().listTextures().length,
    textureMaxSize: lod.textureSize,
    maxDistanceM: lod.maxDistanceM,
    bounds: {
      min: bounds.min.map((value) => Number(value.toFixed(3))),
      // Record measured artifact bounds. The validator must catch a broken
      // normalization rather than replacing evidence with the desired value.
      max: bounds.max.map((value) => Number(value.toFixed(3))),
    },
    nominalRadiusM: Number(radius.toFixed(3)),
    outputPath,
    ktxSourcePath,
  };
}

async function findFile(root, fileName) {
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) {
      const match = await findFile(path, fileName);
      if (match) return match;
    } else if (entry.name === fileName) return path;
  }
  return undefined;
}

async function ensureToktx() {
  const key = `${platform}-${arch}`;
  const release = KTX_TOOLS[key];
  if (!release) throw new Error(`No pinned KTX-Software binary for ${key}`);
  const existing = await findFile(toolDirectory, "toktx").catch(() => undefined);
  if (existing) return existing;
  await mkdir(toolDirectory, { recursive: true });
  const archivePath = join(cacheDirectory, basename(new URL(release.url).pathname));
  await download(release.url, archivePath, release.sha256, "sha256");
  if (release.archive === "pkg") {
    const expanded = join(toolDirectory, "pkg");
    await mkdir(expanded, { recursive: true });
    await execFile("xar", ["-xf", archivePath], { cwd: expanded });
    const packages = (await readdir(expanded)).filter((name) => /-(tools|library)\.pkg$/.test(name));
    for (const packageName of packages) {
      await execFile("bsdtar", ["-xf", join(expanded, packageName, "Payload")], { cwd: toolDirectory });
    }
  } else {
    await execFile("tar", ["-xjf", archivePath, "-C", toolDirectory]);
  }
  const binary = await findFile(toolDirectory, "toktx");
  if (!binary) throw new Error("Pinned KTX-Software package did not contain toktx");
  return binary;
}

async function buildKtx2Variant(entry, toktx) {
  const outputPath = entry.outputPath.replace(/\.glb$/, ".ktx2.glb");
  const firstPass = outputPath.replace(/\.glb$/, ".color.glb");
  const texturePass = outputPath.replace(/\.glb$/, ".texture.glb");
  const environment = {
    ...process.env,
    PATH: `${dirname(toktx)}:${process.env.PATH ?? ""}`,
    DYLD_LIBRARY_PATH: `${join(toolDirectory, "usr/local/lib")}:${process.env.DYLD_LIBRARY_PATH ?? ""}`,
    LD_LIBRARY_PATH: `${join(toolDirectory, "lib")}:${join(toolDirectory, "usr/local/lib")}:${process.env.LD_LIBRARY_PATH ?? ""}`,
  };
  await execFile(process.execPath, [
    gltfTransformCli,
    "uastc",
    entry.ktxSourcePath,
    firstPass,
    "--slots",
    "normalTexture",
    "--level",
    "2",
    "--rdo",
    "--rdo-lambda",
    "0.65",
    "--rdo-multithreading",
    "false",
    "--zstd",
    "18",
    "--jobs",
    "4",
  ], { env: environment, maxBuffer: 16 * 1024 * 1024 });
  await execFile(process.execPath, [
    gltfTransformCli,
    "etc1s",
    firstPass,
    texturePass,
    "--quality",
    "164",
    "--compression",
    "2",
    "--jobs",
    "4",
  ], { env: environment, maxBuffer: 16 * 1024 * 1024 });
  // Texture transforms decode geometry extensions while reading. Re-apply
  // meshopt so the preferred KTX2 artifact retains the same transport budget.
  await execFile(process.execPath, [
    gltfTransformCli,
    "meshopt",
    texturePass,
    outputPath,
    "--level",
    "high",
  ], { env: environment, maxBuffer: 16 * 1024 * 1024 });
  await rm(firstPass, { force: true });
  await rm(texturePass, { force: true });
  await rm(entry.ktxSourcePath, { force: true });
  const buffer = await readFile(outputPath);
  return {
    ktx2Url: `/assets/plant-models/models/${basename(outputPath)}`,
    ktx2Bytes: buffer.length,
    ktx2Sha256: hash(buffer),
  };
}

async function main() {
  await MeshoptEncoder.ready;
  await MeshoptSimplifier.ready;
  await mkdir(modelDirectory, { recursive: true });
  await mkdir(transcoderDirectory, { recursive: true });
  for (const file of await readdir(modelDirectory)) await rm(join(modelDirectory, file), { force: true });
  const threeBasisDirectory = join(webDirectory, "node_modules/three/examples/jsm/libs/basis");
  const transcoderFiles = [];
  for (const fileName of ["basis_transcoder.js", "basis_transcoder.wasm"]) {
    const destination = join(transcoderDirectory, fileName);
    await copyFile(join(threeBasisDirectory, fileName), destination);
    const buffer = await readFile(destination);
    transcoderFiles.push({
      url: `/assets/plant-models/basis/${fileName}`,
      bytes: (await stat(destination)).size,
      sha256: hash(buffer),
    });
  }

  const archetypes = {};
  const records = [];
  for (const source of SOURCES) {
    process.stdout.write(`Preparing ${source.polyHavenName}...\n`);
    const prepared = await prepareSource(source);
    await bakePreparedImpostors(source, prepared);
    const lods = [];
    for (const lodName of Object.keys(LODS)) {
      process.stdout.write(`  ${lodName} LOD...\n`);
      const entry = await buildLod(source, prepared, lodName);
      lods.push(entry);
      records.push(entry);
    }
    const near = lods[0];
    archetypes[source.id] = {
      id: source.id,
      label: source.label,
      nominalHeightM: source.nominalHeightM,
      nominalRadiusM: near.nominalRadiusM,
      nominalCrownDiameterM: Number((near.nominalRadiusM * 2).toFixed(3)),
      origin: "ground-center",
      upAxis: "+Y",
      bounds: near.bounds,
      source: {
        provider: "Poly Haven",
        assetId: source.polyHavenId,
        assetName: source.polyHavenName,
        assetUrl: `https://polyhaven.com/a/${source.polyHavenId}`,
        apiFilesHash: source.filesHash,
        sourceNode: source.sourceNode,
        authors: source.authors,
        license: "CC0-1.0",
        opacityMap: source.opacityMap.key,
        modifications: ["variant selection", "reviewed opacity-map integration", "ground-center normalization", "uniform scale", "component-aware foliage thinning", "LOD simplification", "PBR texture resize", "meshopt compression", "KTX2 transcode"],
        fidelity: "morphological",
        ...(source.id === "pine-natural" ? {
          proxyFor: "Pinus sylvestris",
          proxyReason: "Dense fir variant used as a temporary conifer morphology proxy; reviewed pine source failed live crown-density acceptance.",
        } : {}),
      },
      lods,
    };
  }

  const toktx = await ensureToktx();
  process.stdout.write("Encoding GPU-native KTX2 variants...\n");
  for (const entry of records) Object.assign(entry, await buildKtx2Variant(entry, toktx));

  const transcoderBytes = transcoderFiles.reduce((sum, file) => sum + file.bytes, 0);
  let runtimeBytes = 0;
  let ktx2Bytes = 0;
  let initialFallbackBytes = 0;
  let initialKtx2Bytes = transcoderBytes;
  for (const entry of records) {
    runtimeBytes += entry.bytes;
    ktx2Bytes += entry.ktx2Bytes;
    if (entry.lod === "far") {
      initialFallbackBytes += entry.bytes;
      initialKtx2Bytes += entry.ktx2Bytes;
    }
    delete entry.outputPath;
    delete entry.ktxSourcePath;
    delete entry.nominalRadiusM;
    delete entry.bounds;
  }
  const speciesMap = Object.fromEntries(Object.entries(SPECIES).map(([species, metadata]) => [species, metadata.archetype]));
  const speciesMetadata = Object.fromEntries(Object.entries(SPECIES).map(([species, metadata]) => [species, { ...metadata, fidelity: "morphological" }]));
  const manifest = {
    version: 2,
    schemaVersion: 2,
    libraryVersion: "2.0.0",
    generator: "scripts/build-plant-assets.mjs",
    license: {
      spdx: "CC0-1.0",
      attributionRequired: false,
      notice: "Artist-authored source assets are published by Poly Haven under CC0 1.0. Provenance is recorded per archetype.",
      licenseUrl: "https://polyhaven.com/license",
    },
    format: {
      container: "GLB",
      gltfVersion: "2.0",
      units: "meter",
      upAxis: "+Y",
      origin: "ground-center",
      geometryCompression: "EXT_meshopt_compression",
      textureFallback: "WebP PBR textures embedded in GLB",
      texturePreferred: "KHR_texture_basisu KTX2 (ETC1S colour/ARM; UASTC normal)",
    },
    transcoder: {
      path: "/assets/plant-models/basis/",
      source: "three/examples/jsm/libs/basis",
      files: transcoderFiles,
    },
    lodPolicy: {
      selectionMetric: "camera distance to instance bounding sphere",
      hysteresisM: 5,
      tiers: Object.fromEntries(Object.entries(LODS).map(([name, lod]) => [name, { maxDistanceM: lod.maxDistanceM }])),
    },
    ageScaleProfiles: {
      young: { height: 0.34, crownWidth: 0.28, trunkWidth: 0.55 },
      juvenile: { height: 0.58, crownWidth: 0.5, trunkWidth: 0.72 },
      mature: { height: 1, crownWidth: 1, trunkWidth: 1 },
      veteran: { height: 1.08, crownWidth: 1.18, trunkWidth: 1.16 },
    },
    fidelityStatement: "Four ready-made Poly Haven plant models provide visual archetypes. Species mappings remain morphology-level and must not be presented as surveyed botanical twins.",
    speciesMap,
    speciesMetadata,
    archetypes,
    totals: {
      archetypes: SOURCES.length,
      files: records.length * 2 + transcoderFiles.length,
      runtimeFiles: records.length,
      runtimeBytes,
      initialFallbackBytes,
      ktx2Files: records.length,
      ktx2Bytes,
      initialKtx2Bytes,
      transcoderBytes,
      libraryBytes: runtimeBytes + ktx2Bytes + transcoderBytes,
    },
  };
  await writeFile(join(outputDirectory, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  process.stdout.write(`Built ${SOURCES.length} CC0 archetypes: ${(runtimeBytes / 1024 / 1024).toFixed(2)} MiB fallback + ${(ktx2Bytes / 1024 / 1024).toFixed(2)} MiB KTX2.\n`);
}

await main();
