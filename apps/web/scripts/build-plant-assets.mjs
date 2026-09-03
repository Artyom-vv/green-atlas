import { createHash } from "node:crypto";
import { mkdir, readdir, rm, stat, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as THREE from "three";
import { GLTFExporter } from "three/addons/exporters/GLTFExporter.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const outputDirectory = join(scriptDirectory, "../public/assets/plant-models");
const modelDirectory = join(outputDirectory, "models");

const LODS = {
  // Near models are inspected at worker eye level, so their silhouettes need
  // enough curvature to read as vegetation rather than faceted placeholders.
  near: { radialSegments: 12, foliageDetail: 2, density: 1.12, maxDistanceM: 24 },
  mid: { radialSegments: 8, foliageDetail: 1, density: 0.55, maxDistanceM: 62 },
  far: { radialSegments: 5, foliageDetail: 0, density: 0.24, maxDistanceM: null },
};

const ARCHETYPES = [
  {
    id: "broadleaf-round",
    label: "Овальная лиственная крона",
    form: "oval",
    nominalHeightM: 11,
    nominalCrownDiameterM: 7,
    trunkColor: "#67503a",
    foliageColors: ["#315f3c", "#42794a", "#5a8a54"],
  },
  {
    id: "broadleaf-oval",
    label: "Вытянутая лиственная крона",
    form: "oval",
    nominalHeightM: 12,
    nominalCrownDiameterM: 6.5,
    trunkColor: "#644c37",
    foliageColors: ["#2b5a38", "#3d7345", "#56874d"],
  },
  {
    id: "broadleaf-spreading",
    label: "Раскидистая лиственная крона",
    form: "spreading",
    nominalHeightM: 12,
    nominalCrownDiameterM: 10,
    trunkColor: "#5b4633",
    foliageColors: ["#2c5936", "#3e7442", "#608c4c"],
  },
  {
    id: "broadleaf-columnar",
    label: "Ажурная берёза",
    form: "birch",
    nominalHeightM: 13,
    nominalCrownDiameterM: 6,
    trunkColor: "#dedbd0",
    foliageColors: ["#47713c", "#67914b", "#83a857"],
  },
  {
    id: "broadleaf-irregular",
    label: "Невысокая ажурная крона",
    form: "rowan",
    nominalHeightM: 7,
    nominalCrownDiameterM: 4.8,
    trunkColor: "#674b38",
    foliageColors: ["#365f38", "#527b3e", "#718f43"],
  },
  {
    id: "pine-open",
    label: "Сосна с ярусной кроной",
    form: "pine",
    nominalHeightM: 15,
    nominalCrownDiameterM: 6.5,
    trunkColor: "#71482e",
    foliageColors: ["#1c4e36", "#2b6844", "#3b7650"],
  },
  {
    id: "spruce-dense",
    label: "Плотная еловая крона",
    form: "spruce",
    nominalHeightM: 13,
    nominalCrownDiameterM: 6,
    trunkColor: "#594632",
    foliageColors: ["#183f32", "#245741", "#32684b"],
  },
  {
    id: "conifer",
    label: "Универсальная хвойная крона",
    form: "spruce",
    nominalHeightM: 11,
    nominalCrownDiameterM: 5.4,
    trunkColor: "#594632",
    foliageColors: ["#1a4433", "#285c41", "#37704d"],
  },
  {
    id: "shrub-round",
    label: "Раскидистый кустарник",
    form: "shrub-mounded",
    nominalHeightM: 1.8,
    nominalCrownDiameterM: 2.8,
    trunkColor: "#5a4b35",
    foliageColors: ["#37623c", "#4e7b46", "#70955b"],
  },
  {
    id: "shrub-spreading",
    label: "Вертикальный кустарник",
    form: "shrub-upright",
    nominalHeightM: 2.4,
    nominalCrownDiameterM: 2.1,
    trunkColor: "#58462f",
    foliageColors: ["#315c39", "#477944", "#668f51"],
  },
];

const SPECIES_MAP = {
  "Tilia cordata": { archetype: "broadleaf-round", labelRu: "Липа мелколистная", fidelity: "morphological" },
  "Acer platanoides": { archetype: "broadleaf-oval", labelRu: "Клён остролистный", fidelity: "morphological" },
  "Quercus robur": { archetype: "broadleaf-spreading", labelRu: "Дуб черешчатый", fidelity: "morphological" },
  "Betula pendula": { archetype: "broadleaf-columnar", labelRu: "Берёза повислая", fidelity: "morphological" },
  "Sorbus aucuparia": { archetype: "broadleaf-irregular", labelRu: "Рябина обыкновенная", fidelity: "morphological" },
  "Ulmus laevis": { archetype: "broadleaf-spreading", labelRu: "Вяз гладкий", fidelity: "morphological" },
  "Pinus sylvestris": { archetype: "pine-open", labelRu: "Сосна обыкновенная", fidelity: "morphological" },
  "Picea abies": { archetype: "spruce-dense", labelRu: "Ель европейская", fidelity: "morphological" },
  "Cornus alba": { archetype: "shrub-spreading", labelRu: "Дерён белый", fidelity: "morphological" },
  "Spiraea japonica": { archetype: "shrub-round", labelRu: "Спирея японская", fidelity: "morphological" },
};

class NodeFileReader {
  result = null;
  onloadend = null;

  async readAsArrayBuffer(blob) {
    this.result = await blob.arrayBuffer();
    this.onloadend?.();
  }

  async readAsDataURL(blob) {
    const bytes = Buffer.from(await blob.arrayBuffer());
    this.result = `data:${blob.type};base64,${bytes.toString("base64")}`;
    this.onloadend?.();
  }
}

globalThis.FileReader ??= NodeFileReader;

function seededRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

function hashSeed(value) {
  return [...value].reduce((seed, char) => ((seed * 31) ^ char.charCodeAt(0)) >>> 0, 2166136261);
}

function tint(color, amount) {
  const value = new THREE.Color(color);
  value.offsetHSL(amount * 0.02, amount * 0.025, amount * 0.035);
  return value;
}

function paintGeometry(geometry, color) {
  const count = geometry.getAttribute("position").count;
  const colors = new Uint8Array(count * 3);
  for (let index = 0; index < count; index += 1) {
    colors[index * 3] = Math.round(color.r * 255);
    colors[index * 3 + 1] = Math.round(color.g * 255);
    colors[index * 3 + 2] = Math.round(color.b * 255);
  }
  geometry.deleteAttribute("uv");
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3, true));
  return geometry;
}

function normalizeNormalsSafe(geometry) {
  const normal = geometry.getAttribute("normal");
  const vector = new THREE.Vector3();
  for (let index = 0; index < normal.count; index += 1) {
    vector.fromBufferAttribute(normal, index);
    if (vector.lengthSq() < 1e-12) vector.set(0, 1, 0);
    else vector.normalize();
    normal.setXYZ(index, vector.x, vector.y, vector.z);
  }
  normal.needsUpdate = true;
}

function transformGeometry(geometry, position, scale = [1, 1, 1], rotation = [0, 0, 0]) {
  geometry.scale(...scale);
  geometry.rotateX(rotation[0]);
  geometry.rotateY(rotation[1]);
  geometry.rotateZ(rotation[2]);
  geometry.translate(...position);
  return geometry;
}

function cylinderBetween(start, end, radius, segments, color) {
  const from = new THREE.Vector3(...start);
  const to = new THREE.Vector3(...end);
  const direction = to.clone().sub(from);
  const geometry = new THREE.CylinderGeometry(radius * 0.72, radius, direction.length(), segments, 1, false);
  geometry.applyQuaternion(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.normalize()));
  geometry.translate(...from.clone().add(to).multiplyScalar(0.5).toArray());
  return paintGeometry(geometry, color);
}

function makeCluster(position, scale, detail, color, elongated = false) {
  const geometry = new THREE.IcosahedronGeometry(1, detail);
  return paintGeometry(
    transformGeometry(geometry, position, elongated ? [scale[0], scale[1] * 1.18, scale[2]] : scale),
    color,
  );
}

function selectByDensity(items, density) {
  const count = Math.max(1, Math.round(items.length * density));
  if (count >= items.length) return items;
  const stride = items.length / count;
  return Array.from({ length: count }, (_, index) => items[Math.floor(index * stride)]);
}

function createBroadleafParts(definition, lod, random) {
  const height = definition.nominalHeightM;
  const radius = definition.nominalCrownDiameterM / 2;
  const spreading = definition.form === "spreading";
  const birch = definition.form === "birch";
  const rowan = definition.form === "rowan";
  const trunkTop = height * (spreading ? 0.5 : birch ? 0.48 : rowan ? 0.45 : 0.42);
  const wood = [];
  const crown = [];
  const trunkRadius = radius * (birch ? 0.075 : rowan ? 0.085 : spreading ? 0.11 : 0.09);
  wood.push(cylinderBetween([0, 0, 0], [0, trunkTop + height * 0.12, 0], trunkRadius, lod.radialSegments, new THREE.Color(definition.trunkColor)));

  const branchCount = Math.max(3, Math.round((spreading ? 9 : 7) * lod.density));
  for (let index = 0; index < branchCount; index += 1) {
    const angle = (index / branchCount) * Math.PI * 2 + random() * 0.35;
    const reach = radius * (spreading ? 0.72 : 0.48) * (0.75 + random() * 0.28);
    const startY = trunkTop * (0.72 + random() * 0.2);
    const endY = trunkTop + height * (0.13 + random() * 0.12);
    wood.push(cylinderBetween(
      [0, startY, 0],
      [Math.cos(angle) * reach, endY, Math.sin(angle) * reach],
      trunkRadius * 0.36,
      Math.max(4, lod.radialSegments - 2),
      tint(definition.trunkColor, random() - 0.5),
    ));
  }

  const clusterCount = spreading ? 22 : birch ? 19 : rowan ? 15 : 20;
  const candidates = [];
  for (let index = 0; index < clusterCount; index += 1) {
    const phase = index / clusterCount;
    const angle = phase * Math.PI * 5.6 + random() * 0.6;
    const radial = radius * Math.sqrt(phase) * (spreading ? 0.78 : 0.63);
    const normalizedX = Math.cos(angle) * radial;
    const normalizedZ = Math.sin(angle) * radial;
    const verticalProfile = Math.max(0, 1 - (radial / radius) ** 2);
    const y = trunkTop + height * (spreading ? 0.12 : 0.16) + verticalProfile * height * (birch ? 0.29 : rowan ? 0.24 : 0.25) + (random() - 0.5) * height * 0.05;
    const base = radius * (spreading ? 0.34 : birch ? 0.28 : 0.31) * (0.82 + random() * 0.28);
    candidates.push({
      position: [normalizedX, y, normalizedZ],
      scale: [base * (spreading ? 1.25 : 0.92), base * (birch ? 1.35 : 0.9), base],
      color: tint(definition.foliageColors[index % definition.foliageColors.length], random() - 0.5),
    });
  }
  for (const candidate of selectByDensity(candidates, lod.density)) {
    crown.push(makeCluster(candidate.position, candidate.scale, lod.foliageDetail, candidate.color, birch));
  }
  return { wood, crown };
}

function createConiferParts(definition, lod, random) {
  const height = definition.nominalHeightM;
  const radius = definition.nominalCrownDiameterM / 2;
  const pine = definition.form === "pine";
  const wood = [cylinderBetween([0, 0, 0], [0, height * 0.91, 0], radius * 0.075, lod.radialSegments, new THREE.Color(definition.trunkColor))];
  const crown = [];
  const tierCount = Math.max(3, Math.round((pine ? 9 : 12) * lod.density));

  if (pine) {
    for (let tier = 0; tier < tierCount; tier += 1) {
      const t = tier / Math.max(1, tierCount - 1);
      const y = height * (0.42 + t * 0.5);
      const tierRadius = radius * (0.9 - t * 0.58);
      const clusters = lod === LODS.far ? 2 : Math.max(3, Math.round(4 * lod.density));
      for (let index = 0; index < clusters; index += 1) {
        const angle = (index / clusters) * Math.PI * 2 + tier * 0.62;
        const offset = tierRadius * (0.42 + random() * 0.28);
        crown.push(makeCluster(
          [Math.cos(angle) * offset, y + (random() - 0.5) * height * 0.025, Math.sin(angle) * offset],
          [tierRadius * 0.48, height * 0.055, tierRadius * 0.48],
          lod.foliageDetail,
          tint(definition.foliageColors[(tier + index) % definition.foliageColors.length], random() - 0.5),
        ));
      }
    }
    crown.push(makeCluster([0, height * 0.91, 0], [radius * 0.35, height * 0.1, radius * 0.35], lod.foliageDetail, new THREE.Color(definition.foliageColors[1]), true));
  } else {
    for (let tier = 0; tier < tierCount; tier += 1) {
      const t = tier / Math.max(1, tierCount - 1);
      const y = height * (0.18 + t * 0.72);
      const tierRadius = radius * (1 - t * 0.72);
      const geometry = new THREE.ConeGeometry(tierRadius, height * (0.18 + (1 - t) * 0.05), Math.max(5, lod.radialSegments), 1, false);
      crown.push(paintGeometry(
        transformGeometry(geometry, [0, y, 0], [1, 1, 1], [0, random() * Math.PI, 0]),
        tint(definition.foliageColors[tier % definition.foliageColors.length], random() - 0.5),
      ));
    }
  }
  return { wood, crown };
}

function createShrubParts(definition, lod, random) {
  const height = definition.nominalHeightM;
  const radius = definition.nominalCrownDiameterM / 2;
  const upright = definition.form === "shrub-upright";
  const wood = [];
  const crown = [];
  const stemCount = Math.max(2, Math.round(8 * lod.density));
  for (let index = 0; index < stemCount; index += 1) {
    const angle = (index / stemCount) * Math.PI * 2;
    const reach = radius * 0.42;
    wood.push(cylinderBetween(
      [0, 0, 0],
      [Math.cos(angle) * reach, height * (0.58 + random() * 0.22), Math.sin(angle) * reach],
      radius * 0.035,
      Math.max(4, lod.radialSegments - 2),
      new THREE.Color(definition.trunkColor),
    ));
  }
  const count = upright ? 13 : 16;
  const candidates = [];
  for (let index = 0; index < count; index += 1) {
    const angle = (index / count) * Math.PI * 4.4;
    const radial = radius * Math.sqrt(index / count) * 0.74;
    candidates.push({
      position: [Math.cos(angle) * radial, height * (0.38 + random() * (upright ? 0.48 : 0.34)), Math.sin(angle) * radial],
      scale: [radius * 0.42, height * (upright ? 0.31 : 0.23), radius * 0.42],
      color: tint(definition.foliageColors[index % definition.foliageColors.length], random() - 0.5),
    });
  }
  for (const candidate of selectByDensity(candidates, lod.density)) {
    crown.push(makeCluster(candidate.position, candidate.scale, lod.foliageDetail, candidate.color, upright));
  }
  return { wood, crown };
}

function buildPlant(definition, lodName) {
  const lod = LODS[lodName];
  const random = seededRandom(hashSeed(`${definition.id}:${lodName}`));
  const parts = definition.form.startsWith("shrub")
    ? createShrubParts(definition, lod, random)
    : definition.form === "pine" || definition.form === "spruce"
      ? createConiferParts(definition, lod, random)
      : createBroadleafParts(definition, lod, random);

  const root = new THREE.Group();
  root.name = `${definition.id}_${lodName}`;
  root.userData = {
    archetype: definition.id,
    lod: lodName,
    nominalHeightM: definition.nominalHeightM,
    nominalCrownDiameterM: definition.nominalCrownDiameterM,
    origin: "ground-center",
  };

  const woodGeometry = mergeGeometries(parts.wood, false);
  const crownGeometry = mergeGeometries(parts.crown, false);
  woodGeometry.computeVertexNormals();
  crownGeometry.computeVertexNormals();
  normalizeNormalsSafe(woodGeometry);
  normalizeNormalsSafe(crownGeometry);
  woodGeometry.computeBoundingBox();
  crownGeometry.computeBoundingBox();

  const wood = new THREE.Mesh(woodGeometry, new THREE.MeshStandardMaterial({
    name: "bark",
    color: 0xffffff,
    vertexColors: true,
    roughness: 0.92,
    metalness: 0,
  }));
  wood.name = "wood";
  const crown = new THREE.Mesh(crownGeometry, new THREE.MeshStandardMaterial({
    name: "foliage",
    color: 0xffffff,
    vertexColors: true,
    roughness: 0.86,
    metalness: 0,
  }));
  crown.name = "foliage";
  root.add(wood, crown);
  root.updateMatrixWorld(true);
  let modelBounds = new THREE.Box3().setFromObject(root);
  const groundOffset = modelBounds.min.y;
  if (groundOffset < 0) {
    root.position.y = -groundOffset;
    root.updateMatrixWorld(true);
    modelBounds = new THREE.Box3().setFromObject(root);
  }
  const modelSize = modelBounds.getSize(new THREE.Vector3());
  const modelRadius = Math.max(
    Math.abs(modelBounds.min.x),
    Math.abs(modelBounds.max.x),
    Math.abs(modelBounds.min.z),
    Math.abs(modelBounds.max.z),
  );
  const targetRadius = definition.nominalCrownDiameterM / 2;
  root.scale.set(targetRadius / modelRadius, definition.nominalHeightM / modelSize.y, targetRadius / modelRadius);
  root.updateMatrixWorld(true);
  return root;
}

async function exportGlb(object) {
  const exporter = new GLTFExporter();
  const result = await exporter.parseAsync(object, {
    binary: true,
    onlyVisible: true,
    trs: false,
    maxTextureSize: 1024,
  });
  return Buffer.from(result);
}

function countTriangles(object) {
  let triangles = 0;
  object.traverse((child) => {
    if (!child.isMesh) return;
    const count = child.geometry.index?.count ?? child.geometry.getAttribute("position")?.count ?? 0;
    triangles += Math.floor(count / 3);
  });
  return triangles;
}

function boundsOf(object) {
  const box = new THREE.Box3().setFromObject(object);
  const size = box.getSize(new THREE.Vector3());
  return {
    min: box.min.toArray().map((value) => Number(value.toFixed(3))),
    max: box.max.toArray().map((value) => Number(value.toFixed(3))),
    size: size.toArray().map((value) => Number(value.toFixed(3))),
  };
}

async function cleanGeneratedModels() {
  await mkdir(modelDirectory, { recursive: true });
  const entries = await readdir(modelDirectory);
  await Promise.all(entries.filter((entry) => entry.endsWith(".glb")).map((entry) => rm(join(modelDirectory, entry))));
}

async function main() {
  await cleanGeneratedModels();
  const archetypes = {};
  let totalBytes = 0;

  for (const definition of ARCHETYPES) {
    const variants = {};
    for (const lodName of Object.keys(LODS)) {
      const model = buildPlant(definition, lodName);
      const glb = await exportGlb(model);
      const fileName = `${definition.id}.${lodName}.glb`;
      const filePath = join(modelDirectory, fileName);
      await writeFile(filePath, glb);
      const fileStats = await stat(filePath);
      totalBytes += fileStats.size;
      variants[lodName] = {
        lod: lodName,
        url: `/assets/plant-models/models/${fileName}`,
        bytes: fileStats.size,
        triangleCount: countTriangles(model),
        meshes: 2,
        materials: 2,
        maxDistanceM: LODS[lodName].maxDistanceM,
        sha256: createHash("sha256").update(glb).digest("hex"),
      };
    }
    const referenceModel = buildPlant(definition, "near");
    archetypes[definition.id] = {
      label: definition.label,
      form: definition.form,
      nominalHeightM: definition.nominalHeightM,
      nominalRadiusM: definition.nominalCrownDiameterM / 2,
      nominalCrownDiameterM: definition.nominalCrownDiameterM,
      origin: "ground-center",
      upAxis: "+Y",
      bounds: boundsOf(referenceModel),
      lods: Object.values(variants),
    };
  }

  const manifest = {
    version: 1,
    schemaVersion: 1,
    libraryVersion: "1.0.0",
    generator: "scripts/build-plant-assets.mjs",
    license: {
      spdx: "CC0-1.0",
      attributionRequired: false,
      notice: "Original procedural Green Atlas plant archetypes. No third-party geometry or textures are embedded.",
    },
    format: {
      container: "GLB",
      gltfVersion: "2.0",
      units: "meter",
      upAxis: "+Y",
      origin: "ground-center",
      textures: "none; PBR vertex colors avoid texture memory and alpha overdraw",
    },
    lodPolicy: {
      selectionMetric: "camera distance to instance bounding sphere",
      hysteresisM: 4,
      tiers: Object.fromEntries(Object.entries(LODS).map(([name, value]) => [name, { maxDistanceM: value.maxDistanceM }])),
    },
    ageScaleProfiles: {
      young: { height: 0.34, crownWidth: 0.28, trunkWidth: 0.55 },
      juvenile: { height: 0.58, crownWidth: 0.5, trunkWidth: 0.72 },
      mature: { height: 1, crownWidth: 1, trunkWidth: 1 },
      veteran: { height: 1.08, crownWidth: 1.18, trunkWidth: 1.16 },
    },
    fidelityStatement: "These are morphology-level archetypes, not botanically exact digital twins. Species mapping is explicit and must not be presented as scanned species geometry.",
    speciesMap: Object.fromEntries(Object.entries(SPECIES_MAP).map(([species, value]) => [species, value.archetype])),
    speciesMetadata: SPECIES_MAP,
    archetypes,
    totals: { archetypes: Object.keys(archetypes).length, files: Object.keys(archetypes).length * Object.keys(LODS).length, bytes: totalBytes },
  };
  await writeFile(join(outputDirectory, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  console.log(`Generated ${manifest.totals.files} GLB files (${(totalBytes / 1024).toFixed(1)} KiB).`);
}

await main();
