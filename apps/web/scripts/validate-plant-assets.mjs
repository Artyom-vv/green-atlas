import { createHash } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const assetDirectory = join(scriptDirectory, "../public/assets/plant-models");
const manifestPath = join(assetDirectory, "manifest.json");
const REQUIRED_LODS = ["near", "mid", "far"];
const EXPECTED_ARCHETYPES = [
  "broadleaf-round",
  "broadleaf-oval",
  "broadleaf-spreading",
  "pine-natural",
  "fir-natural",
  "shrub-natural",
];
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
const RUNTIME_SIZE_LIMIT = 8 * 1024 * 1024;
// The distributable library contains both WebP fallback and KTX2 variants;
// a browser loads one branch, never their sum. Keep a repository/storage cap
// while allowing the progressively loaded authored near canopy.
const LIBRARY_SIZE_LIMIT = 32 * 1024 * 1024;
// Near is progressive and screen-critical; the separate runtime/initial-tier
// limits keep startup bounded while allowing the authored canopy to survive.
const FILE_SIZE_LIMITS = { near: 2_000_000, mid: 1_000_000, far: 500_000 };
const TRIANGLE_LIMITS = { near: 15_000, mid: 9_000, far: 3_500 };
// The near tier is intentionally lighter than the scan-derived source. Its
// value comes from a full layered crown silhouette and authored textures, not
// from retaining every photographed leaf card.
const MIN_NEAR_TRIANGLE_RETENTION = 0.08;
const MIN_NEAR_CANOPY_OCCUPANCY = 0.9;
const MIN_NEAR_PROJECTED_CANOPY_DENSITY = 0.08;
const MIN_IMPOSTOR_PROJECTED_CANOPY_DENSITY = 0.55;
// Alpha coverage is measured against the square bake, so narrow, tall conifers
// legitimately occupy less than broadleaf crowns. The upper bound is the
// critical regression guard: a raw atlas/opaque wall cannot pass it.
const MIN_WHOLE_TREE_SILHOUETTE_DENSITY = 0.08;
const MAX_WHOLE_TREE_SILHOUETTE_DENSITY = 0.65;

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function parseGlb(buffer, fileName) {
  invariant(buffer.length >= 20, `${fileName}: file is too short to be GLB`);
  invariant(buffer.readUInt32LE(0) === 0x46546c67, `${fileName}: invalid GLB magic`);
  invariant(buffer.readUInt32LE(4) === 2, `${fileName}: expected glTF 2.0`);
  invariant(buffer.readUInt32LE(8) === buffer.length, `${fileName}: header length mismatch`);
  const jsonLength = buffer.readUInt32LE(12);
  invariant(buffer.readUInt32LE(16) === 0x4e4f534a, `${fileName}: first chunk must be JSON`);
  return JSON.parse(buffer.subarray(20, 20 + jsonLength).toString("utf8").trim());
}

function primitiveTriangles(document, primitive) {
  invariant((primitive.mode ?? 4) === 4, "plant geometry must use TRIANGLES mode");
  const accessorIndex = primitive.indices ?? primitive.attributes.POSITION;
  return Math.floor(document.accessors[accessorIndex].count / 3);
}

function validateDocument(document, fileName, { ktx2, triangleCount, textureCount }) {
  invariant(document.asset?.version === "2.0", `${fileName}: missing glTF 2.0 version`);
  invariant(document.extensionsUsed?.includes("EXT_meshopt_compression"), `${fileName}: meshopt is not declared`);
  invariant(document.extensionsRequired?.includes("EXT_meshopt_compression"), `${fileName}: meshopt must be required`);
  invariant((document.buffers ?? []).every((entry) => !entry.uri), `${fileName}: external buffers are forbidden`);
  invariant((document.images ?? []).every((entry) => !entry.uri), `${fileName}: external images are forbidden`);
  invariant((document.materials?.length ?? 0) >= 1, `${fileName}: missing PBR materials`);
  invariant((document.materials ?? []).every((material) => material.pbrMetallicRoughness), `${fileName}: material is not metallic-roughness PBR`);
  invariant((document.materials ?? []).every((material) => material.alphaMode !== "BLEND"), `${fileName}: blended foliage is forbidden; use MASK`);
  invariant((document.materials ?? []).some((material) => material.alphaMode === "MASK"), `${fileName}: authored foliage cutout is missing`);
  invariant((document.images?.length ?? 0) === textureCount, `${fileName}: texture count does not match manifest`);
  if (ktx2) {
    invariant(document.extensionsUsed?.includes("KHR_texture_basisu"), `${fileName}: KTX2 extension is missing`);
    invariant((document.images ?? []).every((image) => image.mimeType === "image/ktx2"), `${fileName}: non-KTX2 image in preferred variant`);
  } else if (textureCount > 0) {
    invariant(document.extensionsUsed?.includes("EXT_texture_webp"), `${fileName}: WebP fallback extension is missing`);
    invariant((document.images ?? []).every((image) => image.mimeType === "image/webp"), `${fileName}: fallback images must be WebP`);
  }
  for (const primitive of (document.meshes ?? []).flatMap((mesh) => mesh.primitives)) {
    invariant(primitive.attributes.POSITION !== undefined, `${fileName}: primitive has no positions`);
    invariant(primitive.attributes.NORMAL !== undefined, `${fileName}: primitive has no normals`);
    const material = document.materials?.[primitive.material];
    const usesTexture = Boolean(
      material?.pbrMetallicRoughness?.baseColorTexture
      || material?.pbrMetallicRoughness?.metallicRoughnessTexture
      || material?.normalTexture
      || material?.occlusionTexture
      || material?.emissiveTexture,
    );
    if (usesTexture) invariant(primitive.attributes.TEXCOORD_0 !== undefined, `${fileName}: textured primitive has no UVs`);
  }
  const triangles = (document.meshes ?? []).flatMap((mesh) => mesh.primitives)
    .reduce((sum, primitive) => sum + primitiveTriangles(document, primitive), 0);
  invariant(triangles === triangleCount, `${fileName}: triangle count mismatch (${triangles} !== ${triangleCount})`);
}

async function readAndVerify(path, expectedBytes, expectedSha, fileName) {
  const buffer = await readFile(path);
  const fileStats = await stat(path);
  invariant(fileStats.size === expectedBytes, `${fileName}: manifest byte size mismatch`);
  invariant(createHash("sha256").update(buffer).digest("hex") === expectedSha, `${fileName}: SHA-256 mismatch`);
  return buffer;
}

async function main() {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  invariant(manifest.version === 2 && manifest.schemaVersion === 2, "unsupported plant manifest version");
  invariant(manifest.license?.spdx === "CC0-1.0", "plant sources must declare CC0-1.0");
  invariant(manifest.license?.attributionRequired === false, "CC0 sources must not silently require attribution");
  invariant(manifest.license?.licenseUrl === "https://creativecommons.org/publicdomain/zero/1.0/", "missing authoritative CC0 license URL");
  invariant(manifest.format?.gltfVersion === "2.0", "manifest must require glTF 2.0");
  invariant(manifest.format?.units === "meter", "asset units must be meters");
  invariant(manifest.format?.origin === "ground-center", "asset origin must be ground-center");
  invariant(manifest.format?.geometryCompression === "EXT_meshopt_compression", "meshopt contract is missing");

  const archetypeIds = Object.keys(manifest.archetypes ?? {});
  invariant(archetypeIds.length === EXPECTED_ARCHETYPES.length, "exactly six reviewed artist archetypes are required");
  EXPECTED_ARCHETYPES.forEach((id) => invariant(archetypeIds.includes(id), `missing artist archetype ${id}`));
  EXPECTED_SPECIES.forEach((species) => invariant(manifest.speciesMap?.[species], `missing species mapping for ${species}`));

  let runtimeBytes = 0;
  let ktx2Bytes = 0;
  let initialFallbackBytes = 0;
  let initialKtx2Bytes = 0;
  let files = 0;
  for (const [archetypeId, archetype] of Object.entries(manifest.archetypes)) {
    invariant(archetype.nominalHeightM > 0 && archetype.nominalRadiusM > 0, `${archetypeId}: invalid scale contract`);
    invariant(archetype.bounds?.min?.[1] >= -0.001, `${archetypeId}: model extends below ground origin`);
    invariant(Math.abs(archetype.bounds.max[1] - archetype.nominalHeightM) <= 0.1, `${archetypeId}: normalized height mismatch`);
    invariant(archetype.source?.provider === "Quaternius", `${archetypeId}: unreviewed provider`);
    invariant(archetype.source?.license === "CC0-1.0", `${archetypeId}: source license is not CC0`);
    invariant(archetype.source?.assetUrl === "https://quaternius.com/packs/stylizednaturemegakit.html", `${archetypeId}: invalid source URL`);
    invariant(/^[a-f0-9]{64}$/.test(archetype.source?.archiveSha256 ?? ""), `${archetypeId}: missing Quaternius archive lock`);
    invariant(Object.keys(archetype.source?.authors ?? {}).length > 0, `${archetypeId}: missing source authors`);
    invariant(archetype.source?.modifications?.includes("model selection"), `${archetypeId}: model selection is not recorded`);
    invariant(archetype.source?.modifications?.includes("same-source mesh simplification"), `${archetypeId}: LOD lineage is not recorded`);
    invariant(archetype.source?.fidelity === "stylized-volumetric", `${archetypeId}: source fidelity must remain explicit`);

    const triangleCounts = {};
    let sourceTriangleCount;
    const nearBounds = archetype.lods?.find((entry) => entry.lod === "near")?.bounds;
    invariant(nearBounds, `${archetypeId}: missing near bounds`);
    const nearSize = [0, 1, 2].map((axis) => nearBounds.max[axis] - nearBounds.min[axis]);
    for (const lodName of REQUIRED_LODS) {
      const lod = archetype.lods?.find((entry) => entry.lod === lodName);
      invariant(lod, `${archetypeId}: missing ${lodName} LOD`);
      invariant(lod.url.startsWith("/assets/plant-models/models/") && !lod.url.includes(".."), `${archetypeId}/${lodName}: unsafe fallback URL`);
      if (lod.ktx2Url) invariant(lod.ktx2Url.startsWith("/assets/plant-models/models/") && !lod.ktx2Url.includes(".."), `${archetypeId}/${lodName}: unsafe KTX2 URL`);
      invariant(lod.textureCount >= 0, `${archetypeId}/${lodName}: invalid texture count`);
      invariant(lod.bytes <= FILE_SIZE_LIMITS[lodName], `${archetypeId}/${lodName}: fallback file exceeds budget`);
      invariant(lod.triangleCount <= TRIANGLE_LIMITS[lodName], `${archetypeId}/${lodName}: triangle budget exceeded`);
      invariant(Number.isInteger(lod.sourceTriangleCount) && lod.sourceTriangleCount > 0, `${archetypeId}/${lodName}: missing source triangle count`);
      invariant(lod.sourceTriangleCount >= lod.triangleCount, `${archetypeId}/${lodName}: LOD exceeds reviewed source geometry`);
      invariant(Math.abs(lod.triangleRetention - lod.triangleCount / lod.sourceTriangleCount) <= 0.000001, `${archetypeId}/${lodName}: invalid triangle retention`);
      sourceTriangleCount ??= lod.sourceTriangleCount;
      invariant(sourceTriangleCount === lod.sourceTriangleCount, `${archetypeId}: LODs were not built from the same reviewed source`);
      invariant(lod.foliageRepresentation === "artist-authored-alpha-mask", `${archetypeId}/${lodName}: artist-authored foliage must be preserved`);
      invariant(lod.bounds.min[1] >= -0.01 && lod.bounds.min[1] <= 0.2, `${archetypeId}/${lodName}: LOD is not ground anchored`);
      if (lodName === "near") invariant(Math.abs(lod.bounds.max[1] - archetype.nominalHeightM) <= 0.1, `${archetypeId}: near height changed`);
      for (let axis = 0; axis < 3; axis += 1) {
        const ratio = (lod.bounds.max[axis] - lod.bounds.min[axis]) / nearSize[axis];
        invariant(ratio >= 0.85 && ratio <= 1.05, `${archetypeId}/${lodName}: authored silhouette extent changed on axis ${axis}`);
      }

      const fallbackPath = join(assetDirectory, lod.url.replace("/assets/plant-models/", ""));
      const fallbackBuffer = await readAndVerify(fallbackPath, lod.bytes, lod.sha256, `${archetypeId}.${lodName}.glb`);
      validateDocument(parseGlb(fallbackBuffer, fallbackPath), fallbackPath, lod);
      runtimeBytes += lod.bytes;
      if (lodName === "far") initialFallbackBytes += lod.bytes;
      files += 1;

      if (lod.ktx2Url) {
        const ktx2Path = join(assetDirectory, lod.ktx2Url.replace("/assets/plant-models/", ""));
        const ktx2Buffer = await readAndVerify(ktx2Path, lod.ktx2Bytes, lod.ktx2Sha256, `${archetypeId}.${lodName}.ktx2.glb`);
        validateDocument(parseGlb(ktx2Buffer, ktx2Path), ktx2Path, { ...lod, ktx2: true });
        ktx2Bytes += lod.ktx2Bytes;
        if (lodName === "far") initialKtx2Bytes += lod.ktx2Bytes;
        files += 1;
      }
      triangleCounts[lodName] = lod.triangleCount;
    }
    invariant(triangleCounts.near >= triangleCounts.mid, `${archetypeId}: near LOD must be at least as detailed as mid`);
    invariant(triangleCounts.mid >= triangleCounts.far, `${archetypeId}: mid LOD cannot be less detailed than far`);
  }

  if (manifest.transcoder) invariant(manifest.transcoder.path === "/assets/plant-models/basis/", "unexpected KTX2 transcoder path");
  let transcoderBytes = 0;
  for (const transcoder of manifest.transcoder?.files ?? []) {
    invariant(transcoder.url.startsWith("/assets/plant-models/basis/") && !transcoder.url.includes(".."), "unsafe transcoder URL");
    const transcoderPath = join(assetDirectory, transcoder.url.replace("/assets/plant-models/", ""));
    await readAndVerify(transcoderPath, transcoder.bytes, transcoder.sha256, transcoder.url);
    transcoderBytes += transcoder.bytes;
    files += 1;
  }
  initialKtx2Bytes += transcoderBytes;

  invariant(runtimeBytes === manifest.totals.runtimeBytes, "runtime byte total mismatch");
  invariant(ktx2Bytes === manifest.totals.ktx2Bytes, "KTX2 byte total mismatch");
  invariant(transcoderBytes === manifest.totals.transcoderBytes, "transcoder byte total mismatch");
  invariant(initialFallbackBytes === manifest.totals.initialFallbackBytes, "initial fallback byte total mismatch");
  invariant(initialKtx2Bytes === manifest.totals.initialKtx2Bytes, "initial KTX2 byte total mismatch");
  invariant(runtimeBytes + ktx2Bytes + transcoderBytes === manifest.totals.libraryBytes, "library byte total mismatch");
  invariant(files === manifest.totals.files, "file total mismatch");
  invariant(initialFallbackBytes <= RUNTIME_SIZE_LIMIT, `${initialFallbackBytes} bytes exceeds the 8 MiB initial fallback budget`);
  invariant(initialKtx2Bytes <= RUNTIME_SIZE_LIMIT, `${initialKtx2Bytes} bytes exceeds the 8 MiB initial KTX2 budget`);
  invariant(runtimeBytes + ktx2Bytes + transcoderBytes <= LIBRARY_SIZE_LIMIT, `${runtimeBytes + ktx2Bytes + transcoderBytes} bytes exceeds the 64 MiB library budget`);
  console.log(`Validated ${files} files from 6 Quaternius CC0 game-ready assets: ${(initialFallbackBytes / 1024 / 1024).toFixed(2)} MiB first tier, ${(manifest.totals.libraryBytes / 1024 / 1024).toFixed(2)} MiB library.`);
}

await main();
