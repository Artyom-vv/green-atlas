import { createHash } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const assetDirectory = join(scriptDirectory, "../public/assets/plant-models");
const manifestPath = join(assetDirectory, "manifest.json");
const REQUIRED_LODS = ["near", "mid", "far"];
const EXPECTED_SPECIES = [
  "Tilia cordata",
  "Acer platanoides",
  "Quercus robur",
  "Betula pendula",
  "Sorbus aucuparia",
  "Ulmus laevis",
  "Pinus sylvestris",
  "Picea abies",
  "Cornus alba",
  "Spiraea japonica",
];
const RUNTIME_FALLBACK_ARCHETYPES = [
  "broadleaf-columnar",
  "broadleaf-irregular",
  "broadleaf-oval",
  "broadleaf-round",
  "broadleaf-spreading",
  "conifer",
  "shrub-round",
  "shrub-spreading",
];
const SIZE_LIMITS = { near: 700_000, mid: 180_000, far: 80_000 };
const TRIANGLE_LIMITS = { near: 30_000, mid: 8_000, far: 1_500 };
const TOTAL_SIZE_LIMIT = 4_000_000;

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function parseGlb(buffer, fileName) {
  invariant(buffer.length >= 20, `${fileName}: file is too short to be GLB`);
  invariant(buffer.readUInt32LE(0) === 0x46546c67, `${fileName}: invalid GLB magic`);
  invariant(buffer.readUInt32LE(4) === 2, `${fileName}: expected glTF 2.0`);
  invariant(buffer.readUInt32LE(8) === buffer.length, `${fileName}: header length does not match file size`);
  const jsonLength = buffer.readUInt32LE(12);
  invariant(buffer.readUInt32LE(16) === 0x4e4f534a, `${fileName}: first GLB chunk must be JSON`);
  return JSON.parse(buffer.subarray(20, 20 + jsonLength).toString("utf8").trim());
}

function primitiveTriangles(document, primitive) {
  const mode = primitive.mode ?? 4;
  invariant(mode === 4, "plant geometry must use TRIANGLES mode");
  const accessorIndex = primitive.indices ?? primitive.attributes.POSITION;
  return Math.floor(document.accessors[accessorIndex].count / 3);
}

async function main() {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  invariant(manifest.version === 1 && manifest.schemaVersion === 1, "unsupported manifest version");
  invariant(manifest.license?.spdx === "CC0-1.0", "plant library must declare an SPDX license");
  invariant(manifest.license?.attributionRequired === false, "generated library must not silently require attribution");
  invariant(manifest.format?.gltfVersion === "2.0", "manifest must require glTF 2.0");
  invariant(manifest.format?.units === "meter", "asset units must be meters");
  invariant(manifest.format?.origin === "ground-center", "asset origin must be ground-center");
  invariant(Object.keys(manifest.archetypes ?? {}).length >= 8, "at least eight visibly distinct plant archetypes are required");
  for (const species of EXPECTED_SPECIES) {
    invariant(manifest.speciesMap?.[species], `missing catalog mapping for ${species}`);
  }

  let totalBytes = 0;
  const archetypeIds = new Set(Object.keys(manifest.archetypes));
  for (const archetype of RUNTIME_FALLBACK_ARCHETYPES) {
    invariant(archetypeIds.has(archetype), `missing runtime fallback archetype ${archetype}`);
  }
  for (const [species, mapping] of Object.entries(manifest.speciesMap)) {
    invariant(archetypeIds.has(mapping), `${species}: unknown archetype ${mapping}`);
    invariant(manifest.speciesMetadata?.[species]?.fidelity === "morphological", `${species}: fidelity must remain explicit`);
  }

  for (const [archetypeId, archetype] of Object.entries(manifest.archetypes)) {
    archetype.id = archetypeId;
    invariant(archetype.nominalHeightM > 0, `${archetype.id}: missing nominal height`);
    invariant(archetype.nominalCrownDiameterM > 0, `${archetype.id}: missing nominal crown diameter`);
    invariant(archetype.bounds?.min?.[1] >= -0.001, `${archetype.id}: model extends below its ground origin`);
    invariant(Math.abs(archetype.bounds.max[1] - archetype.nominalHeightM) <= 0.005, `${archetype.id}: bounds do not match nominal height`);
    const horizontalRadius = Math.max(
      Math.abs(archetype.bounds.min[0]),
      Math.abs(archetype.bounds.max[0]),
      Math.abs(archetype.bounds.min[2]),
      Math.abs(archetype.bounds.max[2]),
    );
    invariant(Math.abs(horizontalRadius - archetype.nominalRadiusM) <= 0.002, `${archetype.id}: bounds do not match nominal radius`);
    for (const lodName of REQUIRED_LODS) {
      const lod = archetype.lods?.find((entry) => entry.lod === lodName);
      invariant(lod, `${archetype.id}: missing ${lodName} LOD`);
      invariant(lod.url.startsWith("/assets/plant-models/models/") && !lod.url.includes(".."), `${archetype.id}/${lodName}: unsafe URL`);
      const filePath = join(assetDirectory, lod.url.replace("/assets/plant-models/", ""));
      const buffer = await readFile(filePath);
      const fileStats = await stat(filePath);
      totalBytes += fileStats.size;
      invariant(fileStats.size === lod.bytes, `${archetype.id}/${lodName}: manifest byte size mismatch`);
      invariant(fileStats.size <= SIZE_LIMITS[lodName], `${archetype.id}/${lodName}: ${fileStats.size} bytes exceeds budget`);
      invariant(createHash("sha256").update(buffer).digest("hex") === lod.sha256, `${archetype.id}/${lodName}: SHA-256 mismatch`);

      const document = parseGlb(buffer, `${archetype.id}.${lodName}.glb`);
      invariant(document.asset?.version === "2.0", `${archetype.id}/${lodName}: missing glTF asset version`);
      invariant((document.buffers ?? []).every((entry) => !entry.uri), `${archetype.id}/${lodName}: external buffers are forbidden`);
      invariant((document.images ?? []).every((entry) => !entry.uri), `${archetype.id}/${lodName}: external images are forbidden`);
      invariant((document.meshes?.length ?? 0) === 2, `${archetype.id}/${lodName}: expected exactly two instancing parts`);
      invariant((document.materials?.length ?? 0) === 2, `${archetype.id}/${lodName}: expected bark and foliage materials`);
      invariant((document.materials ?? []).every((material) => material.pbrMetallicRoughness), `${archetype.id}/${lodName}: all materials must be PBR`);
      for (const primitive of (document.meshes ?? []).flatMap((mesh) => mesh.primitives)) {
        invariant(primitive.attributes.NORMAL !== undefined, `${archetype.id}/${lodName}: primitive has no normals`);
        invariant(primitive.attributes.TEXCOORD_0 === undefined, `${archetype.id}/${lodName}: unused UV data must be stripped`);
        const colorAccessor = document.accessors[primitive.attributes.COLOR_0];
        invariant(colorAccessor?.componentType === 5121 && colorAccessor.normalized === true, `${archetype.id}/${lodName}: colors must use normalized U8`);
      }
      const triangles = (document.meshes ?? []).flatMap((mesh) => mesh.primitives).reduce((sum, primitive) => sum + primitiveTriangles(document, primitive), 0);
      invariant(triangles === lod.triangleCount, `${archetype.id}/${lodName}: triangle count mismatch (${triangles} !== ${lod.triangleCount})`);
      invariant(triangles <= TRIANGLE_LIMITS[lodName], `${archetype.id}/${lodName}: ${triangles} triangles exceeds budget`);
    }
    const triangleCounts = Object.fromEntries(archetype.lods.map((entry) => [entry.lod, entry.triangleCount]));
    invariant(triangleCounts.near > triangleCounts.mid, `${archetype.id}: near LOD must be more detailed than mid`);
    invariant(triangleCounts.mid > triangleCounts.far, `${archetype.id}: mid LOD must be more detailed than far`);
  }

  invariant(totalBytes === manifest.totals.bytes, "manifest total byte size mismatch");
  invariant(totalBytes <= TOTAL_SIZE_LIMIT, `${totalBytes} total bytes exceeds ${TOTAL_SIZE_LIMIT} byte library budget`);
  console.log(`Validated ${manifest.totals.files} GLBs, ${Object.keys(manifest.archetypes).length} archetypes, ${(totalBytes / 1024).toFixed(1)} KiB total.`);
}

await main();
