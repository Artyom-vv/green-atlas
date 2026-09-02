import { useEffect, useRef, useState } from 'react';
import type { ScenePlantObject, SceneSnapshot } from '@green/api-client';
import { Button, Checkbox, InlineMessage } from '@green/ui';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GrowthHorizonSlider } from './GrowthHorizonControl';

type LocalPoint = [number, number];
type SceneContextFeature = { feature_id: string; kind: string; geometry: { [key: string]: unknown }; label?: string | null };
function ringPoints(value: unknown): LocalPoint[] {
  return Array.isArray(value) ? value.filter((point): point is number[] => Array.isArray(point) && typeof point[0] === 'number' && typeof point[1] === 'number').map((point) => [point[0], point[1]]) : [];
}

function addContextFeature(scene: THREE.Scene, feature: SceneContextFeature) {
  const geometry = feature.geometry as { type?: string; coordinates?: unknown };
  const coordinates = geometry.coordinates;
  const color = feature.kind === 'road' ? 0x8996a3 : feature.kind === 'building' ? 0x9aa8bb : feature.kind === 'water' ? 0x4e9fc4 : feature.kind === 'existing_green' ? 0x5eaa7b : feature.kind === 'utility' ? 0xc58b40 : 0x5c8be8;
  const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity: feature.kind === 'building' ? .72 : .58 });
  const drawLine = (points: LocalPoint[]) => {
    if (points.length < 2) return;
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points.map(([x, y]) => new THREE.Vector3(x, .03, -y))), material);
    scene.add(line);
  };
  const drawPolygon = (points: LocalPoint[]) => {
    if (points.length < 3) return;
    const shape = new THREE.Shape(points.map(([x, y]) => new THREE.Vector2(x, -y)));
    const meshGeometry = feature.kind === 'building'
      ? new THREE.ExtrudeGeometry(shape, { depth: 1.2, bevelEnabled: false })
      : new THREE.ShapeGeometry(shape);
    const mesh = new THREE.Mesh(meshGeometry, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: feature.kind === 'building' ? .3 : .12, side: THREE.DoubleSide }));
    mesh.rotation.x = -Math.PI / 2;
    mesh.position.y = feature.kind === 'building' ? .08 : .01;
    scene.add(mesh);
    drawLine([...points, points[0]]);
  };
  if (geometry.type === 'LineString') drawLine(ringPoints(coordinates));
  else if (geometry.type === 'MultiLineString') (Array.isArray(coordinates) ? coordinates : []).forEach((line) => drawLine(ringPoints(line)));
  else if (geometry.type === 'Polygon') drawPolygon(ringPoints(Array.isArray(coordinates) ? coordinates[0] : []));
  else if (geometry.type === 'MultiPolygon') (Array.isArray(coordinates) ? coordinates : []).forEach((polygon) => drawPolygon(ringPoints(Array.isArray(polygon) ? polygon[0] : [])));
}

type SceneReviewProps = { snapshot?: SceneSnapshot };

function crownGeometry(object: ScenePlantObject, radius: number, height: number): THREE.BufferGeometry {
  switch (object.crown_shape) {
    case 'conical': return new THREE.ConeGeometry(radius, height, 12);
    case 'columnar': return new THREE.CylinderGeometry(radius * .62, radius * .8, height, 12);
    case 'irregular': return new THREE.DodecahedronGeometry(radius, 1);
    default: return new THREE.SphereGeometry(radius, 14, 9);
  }
}

export function SceneReview({ snapshot, horizon, selectedIds, loading, error, onHorizon, onSelect, onClose }: {
  snapshot?: SceneSnapshot;
  horizon: number;
  selectedIds: string[];
  loading?: boolean;
  error?: string;
  onHorizon: (horizon: number) => void;
  onSelect: (objectId: string) => void;
  onClose: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const onSelectRef = useRef(onSelect);
  const [showRoots, setShowRoots] = useState(false);
  const [renderError, setRenderError] = useState<string>();
  const selectedIdsKey = [...selectedIds].sort().join(':');
  onSelectRef.current = onSelect;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !snapshot) return;
    const selected = new Set(selectedIdsKey ? selectedIdsKey.split(':') : []);
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' });
    } catch {
      setRenderError('3D недоступен в этом браузере. План остаётся доступен в 2D.');
      return;
    }
    setRenderError(undefined);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
    renderer.setClearColor(0xf4f6f8, 1);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, .1, 10000);
    const maxDistance = Math.max(18, ...snapshot.objects.map((object) => Math.hypot(object.local_x, object.local_y) + object.canopy_radius_max_m));
    camera.position.set(maxDistance * .9, maxDistance * .85, maxDistance * 1.05);
    const controls = new OrbitControls(camera, canvas);
    controls.target.set(0, 0, 0);
    controls.enableDamping = true;
    controls.dampingFactor = .08;
    controls.maxPolarAngle = Math.PI / 2.05;
    controls.minDistance = 4;
    controls.maxDistance = maxDistance * 5;
    controls.update();

    scene.add(new THREE.HemisphereLight(0xffffff, 0xd7dde4, 2.1));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(maxDistance, maxDistance * 1.5, maxDistance * .6);
    scene.add(sun);
    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(maxDistance * 4, maxDistance * 4),
      new THREE.MeshLambertMaterial({ color: 0xf8fafb, side: THREE.DoubleSide }),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -.03;
    scene.add(ground);
    const grid = new THREE.GridHelper(maxDistance * 4, Math.min(80, Math.max(20, Math.round(maxDistance * 2))), 0xc7d0da, 0xdfe5eb);
    scene.add(grid);

    // The DXF is the ground truth context. Draw its relevant footprints in
    // the same local coordinate frame as plantings; never invent terrain or
    // underground geometry here.
    for (const feature of snapshot.context_features ?? []) addContextFeature(scene, feature);

    for (const object of snapshot.objects) {
      const group = new THREE.Group();
      group.userData.objectId = object.object_id;
      group.position.set(object.local_x, 0, -object.local_y);
      const radius = Math.max(.3, object.canopy_radius_max_m);
      const totalHeight = object.height_max_m ?? (object.kind === 'tree' ? Math.max(4, radius * 2.4) : Math.max(1, radius * 1.2));
      const crownHeight = object.kind === 'tree' ? Math.max(1.4, Math.min(totalHeight * .72, radius * 2)) : Math.max(.6, Math.min(totalHeight, radius * 1.25));
      const trunkHeight = object.kind === 'tree' ? Math.max(.8, totalHeight - crownHeight * .72) : .15;
      if (object.kind === 'tree') {
        const trunk = new THREE.Mesh(new THREE.CylinderGeometry(.12, .18, trunkHeight, 8), new THREE.MeshLambertMaterial({ color: 0x6f5a45 }));
        trunk.position.y = trunkHeight / 2;
        group.add(trunk);
      }
      const crown = new THREE.Mesh(
        crownGeometry(object, radius, crownHeight),
        new THREE.MeshLambertMaterial({ color: selected.has(object.object_id) ? 0x225cff : object.kind === 'tree' ? 0x26885a : 0x72b892, transparent: true, opacity: object.confidence === 'unknown' ? .68 : .88 }),
      );
      if (object.crown_shape === 'oval') crown.scale.y = 1.25;
      if (object.crown_shape === 'spreading') crown.scale.y = .68;
      crown.position.y = object.kind === 'tree' ? trunkHeight + crownHeight * .32 : crownHeight / 2;
      group.add(crown);
      if (showRoots && object.root_radius_max_m) {
        const roots = new THREE.Mesh(
          new THREE.RingGeometry(Math.max(.05, object.root_radius_min_m ?? object.root_radius_max_m * .65), object.root_radius_max_m, 32),
          new THREE.MeshBasicMaterial({ color: 0xb76400, transparent: true, opacity: .22, side: THREE.DoubleSide }),
        );
        roots.rotation.x = -Math.PI / 2;
        roots.position.y = .02;
        group.add(roots);
      }
      scene.add(group);
    }

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const handleClick = (event: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(scene.children, true).find((candidate) => {
        let node: THREE.Object3D | null = candidate.object;
        while (node) {
          if (node.userData.objectId) return true;
          node = node.parent;
        }
        return false;
      });
      let node: THREE.Object3D | null = hit?.object ?? null;
      while (node && !node.userData.objectId) node = node.parent;
      if (node?.userData.objectId) onSelectRef.current(String(node.userData.objectId));
    };
    canvas.addEventListener('click', handleClick);
    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      renderer.setSize(rect.width, rect.height, false);
      camera.aspect = rect.width / rect.height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();
    let frame = 0;
    const render = () => {
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(render);
    };
    render();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      canvas.removeEventListener('click', handleClick);
      controls.dispose();
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          object.geometry.dispose();
          const materials = Array.isArray(object.material) ? object.material : [object.material];
          materials.forEach((material) => material.dispose());
        }
      });
      renderer.dispose();
    };
  }, [selectedIdsKey, showRoots, snapshot]);

  return <section className="scene-review" aria-label="Параметрический 3D-предпросмотр">
    <header className="scene-review__header">
      <span><strong>3D-предпросмотр</strong><small>Упрощённая параметрическая сцена</small><small>DXF-контекст</small></span>
      <div className="scene-review__horizons" aria-label="Горизонт роста"><GrowthHorizonSlider value={horizon} onChange={(next) => { if (next !== undefined) onHorizon(next); }} />{([0, 5, 10, 20] as const).map((value) => <Button key={value} variant={horizon === value ? 'primary' : 'ghost'} controlSize="compact" onClick={() => onHorizon(value)}>{value === 0 ? 'Сейчас' : `${value} лет`}</Button>)}</div>
      <Checkbox label="Корни" checked={showRoots} onChange={(event) => setShowRoots(event.target.checked)} />
      <Button variant="secondary" controlSize="compact" onClick={onClose}>Вернуться к карте</Button>
    </header>
    <div className="scene-review__viewport"><canvas ref={canvasRef} aria-label="3D-сцена посадок" />{loading ? <div className="scene-review__status">Готовим сцену</div> : null}{error || renderError ? <div className="scene-review__message"><InlineMessage tone="error">{error ?? renderError}</InlineMessage></div> : null}</div>
    <footer><span>{snapshot?.objects.length ?? 0} посадок</span><span>Выбрано: {selectedIds.length}</span><span className="scene-review__legend"><i className="is-road" /> дороги <i className="is-building" /> здания <i className="is-water" /> вода</span><span>Рельеф и высоты зданий не заданы</span></footer>
  </section>;
}
