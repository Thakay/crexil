// The mesh lightbox's stage: everything that touches three, in a
// lazy chunk (Lightbox.tsx imports it with `lazy`, so the main bundle never
// carries a renderer). The shell (chrome, poster, states, the mode row)
// is Lightbox.tsx's; this file loads, draws and reports.
//
// Loading: GLTFLoader over `/nodes/{nid}/image`, the full-res artefact.
// Byte progress feeds the poster's bar; the fetch is aborted if the
// lightbox closes first, and every geometry, material and texture is
// disposed on unmount — ~100 MB of buffers per open otherwise retained
// (R3F disposes the renderer with the Canvas). `run_trellis.py` exports
// EXT_texture_webp, which GLTFLoader reads on its own; the draco/meshopt
// decoders stay off — they default to a CDN, and nothing we write uses
// them.
//
// Modes are view state applied to the loaded scene — the artefact is never
// touched. NORMALS swaps every material for a normal material, CLAY for one
// neutral standard material, BACKFACE CULL flips `side` (off = DoubleSide,
// the shell looks closed; on = FrontSide, the hole shows), WIREFRAME is the
// material flag, GRID a ground plane at the mesh's foot sized from its
// bounds. The camera frames from bounds: ~20°
// above and a little to the side, the orbit unclamped so underneath is one
// drag away; FROM BELOW is the same distance from −35°, RESET VIEW the
// opening framing. Left-drag orbits, wheel zooms, right-drag pans — the
// hint's words are OrbitControls' defaults.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { Canvas, useThree } from "@react-three/fiber";
import { Grid, OrbitControls } from "@react-three/drei";
import type { LoadStatus, View } from "./Lightbox";

type Props = {
  url: string;
  view: View;
  onLoad: (st: LoadStatus) => void;
  onBelow: (below: boolean) => void;
};

type Bounds = { center: THREE.Vector3; radius: number; floor: number };
type Loaded = { scene: THREE.Group; bounds: Bounds };
type Mats = {
  clay: THREE.MeshStandardMaterial;
  normals: THREE.MeshNormalMaterial;
  wire: THREE.MeshBasicMaterial;
};
type Original = THREE.Material | THREE.Material[];

function boundsOf(scene: THREE.Object3D): Bounds {
  const box = new THREE.Box3().setFromObject(scene);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  return { center, radius: Math.max(size.length() / 2, 1e-3), floor: box.min.y };
}

/** Faces in the file — the harness's GLB has none, and says so honestly. */
function triangles(scene: THREE.Object3D): number {
  let n = 0;
  scene.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh || m.userData.overlay) return;
    const g = m.geometry;
    n += g.index ? g.index.count / 3 : (g.getAttribute("position")?.count ?? 0) / 3;
  });
  return n;
}

function disposeScene(scene: THREE.Object3D): void {
  scene.traverse((o) => {
    const m = o as THREE.Mesh;
    // the wire overlay shares its parent's geometry and the stage's material
    if (!m.isMesh || m.userData.overlay) return;
    m.geometry.dispose();
    // the file's own materials, whichever mode was on at close
    const orig = (m.userData.original as Original | undefined) ?? m.material;
    for (const mat of Array.isArray(orig) ? orig : [orig]) {
      for (const v of Object.values(mat))
        if ((v as THREE.Texture)?.isTexture) (v as THREE.Texture).dispose();
      mat.dispose();
    }
  });
}

/** The GLB, with byte progress on the way; aborted and disposed on cleanup.
 *  The bytes are fetched here, not by GLTFLoader's FileLoader: that one
 *  dedupes in-flight requests per URL module-wide, so a second open joining
 *  a first that then aborts (StrictMode's mount → cleanup → mount, or a
 *  fast close-and-reopen) would fail with the abort it never asked for.
 *  Our own fetch has its own signal; the loader only parses. */
function useGlb(url: string, onLoad: Props["onLoad"]): Loaded | null {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const report = useRef(onLoad);
  report.current = onLoad;
  useEffect(() => {
    let live = true;
    let scene: THREE.Group | null = null;
    const ctl = new AbortController();
    const fail = (err: unknown) => {
      if (!live) return;
      if (err instanceof DOMException && err.name === "AbortError") return;
      report.current({
        status: "failed",
        error: err instanceof Error ? err.message : String(err),
      });
    };
    report.current({ status: "loading", loaded: 0, total: 0 });
    (async () => {
      const res = await fetch(url, { signal: ctl.signal });
      if (!res.ok || res.body === null)
        throw new Error(`the artefact route answered ${res.status}`);
      const total = Number(res.headers.get("content-length") ?? 0);
      const reader = res.body.getReader();
      const chunks: Uint8Array[] = [];
      let bytes = 0;
      let step = -1;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        bytes += value.byteLength;
        // once per percent, or per half-megabyte when the length is unknown
        const next =
          total > 0
            ? Math.floor((bytes / total) * 100)
            : Math.floor(bytes / 524_288);
        if (next !== step && live) {
          step = next;
          report.current({ status: "loading", loaded: bytes, total });
        }
      }
      if (!live) return;
      const buf = new Uint8Array(bytes);
      let off = 0;
      for (const c of chunks) {
        buf.set(c, off);
        off += c.byteLength;
      }
      new GLTFLoader().parse(
        buf.buffer,
        "",
        (gltf) => {
          if (!live) {
            disposeScene(gltf.scene);
            return;
          }
          scene = gltf.scene;
          if (triangles(scene) === 0) {
            report.current({ status: "empty" });
            return;
          }
          setLoaded({ scene, bounds: boundsOf(scene) });
          report.current({ status: "ready" });
        },
        fail,
      );
    })().catch(fail);
    return () => {
      live = false;
      ctl.abort();
      if (scene !== null) disposeScene(scene);
      setLoaded(null);
    };
  }, [url]);
  return loaded;
}

/** The file's meshes, wearing the view: material by mode, side by cull,
 *  and WIREFRAME as an overlay — topology drawn OVER the surface in the
 *  accent, a child mesh sharing the geometry. The material flag alone
 *  reads as a solid at a million triangles; over the surface the density
 *  and the stretched triangles are what you see. The surface is pushed
 *  back a hair while the wire is on so the lines win the depth test. */
function Model({ scene, view, mats }: { scene: THREE.Group; view: View; mats: Mats }) {
  useEffect(() => {
    const side = view.cull ? THREE.FrontSide : THREE.DoubleSide;
    const overlays: [THREE.Mesh, THREE.Mesh][] = [];
    scene.traverse((o) => {
      const m = o as THREE.Mesh;
      if (!m.isMesh || m.userData.overlay) return;
      if (m.userData.original === undefined) m.userData.original = m.material;
      const orig = m.userData.original as Original;
      const chosen = view.normals ? mats.normals : view.surface === "clay" ? mats.clay : orig;
      m.material = chosen;
      for (const mat of Array.isArray(chosen) ? chosen : [chosen]) {
        mat.polygonOffset = view.wireframe;
        mat.polygonOffsetFactor = 1;
        mat.polygonOffsetUnits = 1;
        if (mat.side === side) continue;
        mat.side = side;
        mat.needsUpdate = true; // DOUBLE_SIDED is a shader define
      }
      let wire = m.userData.wire as THREE.Mesh | undefined;
      if (wire === undefined) {
        wire = new THREE.Mesh(m.geometry, mats.wire);
        wire.userData.overlay = true;
        m.userData.wire = wire;
        overlays.push([m, wire]);
      }
      wire.visible = view.wireframe;
    });
    // added after the walk — a child added mid-traverse is visited too
    for (const [m, w] of overlays) m.add(w);
    if (mats.wire.side !== side) {
      mats.wire.side = side;
      mats.wire.needsUpdate = true;
    }
  }, [scene, view.surface, view.normals, view.cull, view.wireframe, mats]);
  return <primitive object={scene} />;
}

/** Ground and scale: a plane at the mesh's foot, cells a tenth of the
 *  nearest power of ten to its size — a 1 m object gets 10 cm cells. */
function Ground({ bounds }: { bounds: Bounds }) {
  const section = 10 ** Math.floor(Math.log10(bounds.radius * 2));
  return (
    <Grid
      position={[bounds.center.x, bounds.floor, bounds.center.z]}
      args={[section * 20, section * 20]}
      cellSize={section / 10}
      sectionSize={section}
      cellColor="#2f353c"
      sectionColor="#4a5158"
      cellThickness={0.8}
      sectionThickness={1.2}
      fadeDistance={bounds.radius * 9}
      fadeStrength={1.4}
      infiniteGrid
      side={THREE.DoubleSide}
    />
  );
}

/** A fixed, procedural environment (no fetch, no dial: there are no
 *  presentation modes): TRELLIS bakes `metallicFactor: 1` with a
 *  metallic-roughness map, and a metal with nothing to reflect renders
 *  near-black in three. This is what makes the bake visible at all, the
 *  way the engine's own turntable lit it. */
function Env() {
  const gl = useThree((st) => st.gl);
  const scene = useThree((st) => st.scene);
  useEffect(() => {
    const pmrem = new THREE.PMREMGenerator(gl);
    const env = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = env;
    scene.environmentIntensity = 0.7;
    return () => {
      scene.environment = null;
      env.dispose();
      pmrem.dispose();
    };
  }, [gl, scene]);
  return null;
}

type Controls = React.ComponentRef<typeof OrbitControls>;
const AZIMUTH = THREE.MathUtils.degToRad(35);

/** Frames from bounds, applies the presets, reports which side the camera is on. */
function Rig({ bounds, view, onBelow }: { bounds: Bounds; view: View; onBelow: Props["onBelow"] }) {
  const camera = useThree((st) => st.camera) as THREE.PerspectiveCamera;
  const controls = useRef<Controls>(null);
  const distance = useMemo(
    () => (bounds.radius / Math.sin(THREE.MathUtils.degToRad(camera.fov) / 2)) * 1.15,
    [bounds.radius, camera.fov],
  );
  const place = useCallback(
    (elevationDeg: number) => {
      const el = THREE.MathUtils.degToRad(elevationDeg);
      const c = bounds.center;
      camera.position.set(
        c.x + distance * Math.cos(el) * Math.sin(AZIMUTH),
        c.y + distance * Math.sin(el),
        c.z + distance * Math.cos(el) * Math.cos(AZIMUTH),
      );
      camera.near = distance / 100;
      camera.far = distance * 100;
      camera.updateProjectionMatrix();
      const ctl = controls.current;
      if (ctl !== null) {
        ctl.target.copy(c);
        ctl.update();
      }
      onBelow(elevationDeg < 0);
    },
    [bounds.center, camera, distance, onBelow],
  );
  // the opening framing, then each preset as it is asked (presetSeq re-fires
  // the same one; the mount runs with preset null and seq 0)
  useEffect(() => place(20), [place]);
  useEffect(() => {
    if (view.preset === "below") place(-35);
    else if (view.preset === "reset") place(20);
  }, [view.presetSeq]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <OrbitControls
      ref={controls}
      makeDefault
      enableDamping
      dampingFactor={0.12}
      minPolarAngle={0}
      maxPolarAngle={Math.PI}
      minDistance={bounds.radius * 0.25}
      maxDistance={distance * 6}
      onChange={() =>
        onBelow(camera.position.y < (controls.current?.target.y ?? bounds.center.y))
      }
    />
  );
}

export default function MeshStage({ url, view, onLoad, onBelow }: Props) {
  const loaded = useGlb(url, onLoad);
  // one clay, one normals — shared across every mesh in the file, disposed
  // with the stage
  const mats = useMemo<Mats>(
    () => ({
      clay: new THREE.MeshStandardMaterial({ color: "#9ca3a8", roughness: 0.85, metalness: 0 }),
      normals: new THREE.MeshNormalMaterial(),
      wire: new THREE.MeshBasicMaterial({
        color: "#7fb2ff",
        wireframe: true,
        transparent: true,
        // faint on purpose: at a million triangles the wire is denser than
        // the pixels, and the surface has to stay readable under it
        opacity: 0.3,
        depthWrite: false,
      }),
    }),
    [],
  );
  useEffect(
    () => () => {
      mats.clay.dispose();
      mats.normals.dispose();
      mats.wire.dispose();
    },
    [mats],
  );
  return (
    <Canvas
      // no tone mapping: the baked texture as baked — this is inspection,
      // not a render (no presentation modes, on purpose)
      flat
      dpr={[1, 2]}
      camera={{ fov: 40, near: 0.01, far: 100 }}
      gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
      style={{ background: "transparent" }}
    >
      {/* a fixed rig — no light slider: sky/ground fill, a key from
          above, a weak fill from below so the underside is legible */}
      <Env />
      <hemisphereLight args={["#e4e8eb", "#2f353c", 0.8]} />
      <directionalLight position={[3, 6, 4]} intensity={1.6} />
      <directionalLight position={[-4, -2, -3]} intensity={0.5} />
      {loaded !== null && (
        <>
          <Model scene={loaded.scene} view={view} mats={mats} />
          {view.grid && <Ground bounds={loaded.bounds} />}
          <Rig bounds={loaded.bounds} view={view} onBelow={onBelow} />
        </>
      )}
    </Canvas>
  );
}
