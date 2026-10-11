// Cinematic sets: a golden-hour lake and the water below it. Image-based light from a CC0
// sky; the sun is found in the image and the sky is turned so it lights the stage from the
// side the look asks for.
import { HDRLoader } from "three/addons/loaders/HDRLoader.js";
import { Water } from "three/addons/objects/Water.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { mulberry32 } from "./math.mjs";
import { softDot } from "./fx.mjs";

export function sunFromHdr(THREE, hdr) {
  const { data, width, height } = hdr.image;
  let best = -1, bx = 0, by = 0;
  for (let y = 0; y < height / 2; y++) {
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4, l = data[i] * 0.2126 + data[i + 1] * 0.7152 + data[i + 2] * 0.0722;
      if (l > best) { best = l; bx = x; by = y; }
    }
  }
  const u = (bx + 0.5) / width, v = 1 - (by + 0.5) / height;
  const el = (v - 0.5) * Math.PI, phi = (u - 0.5) * 2 * Math.PI;
  return new THREE.Vector3(Math.cos(el) * Math.cos(phi), Math.sin(el), Math.cos(el) * Math.sin(phi)).normalize();
}

async function loadTex(THREE, url, srgb) {
  const t = await new THREE.TextureLoader().loadAsync(url);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

// Light the scene from the sky image; returns the sun direction in world space.
export async function skyLight(THREE, renderer, scene, url, { sunAngle = 80, background = true, intensity = 0.9 } = {}) {
  const hdr = await new HDRLoader().setDataType(THREE.FloatType).loadAsync(url);
  hdr.mapping = THREE.EquirectangularReflectionMapping;
  const sun = sunFromHdr(THREE, hdr);
  // turn the sky so the sun sits `sunAngle` degrees off the camera's view (-z), to the left
  const want = new THREE.Vector3(0, 0, -1).applyAxisAngle(new THREE.Vector3(0, 1, 0), (sunAngle * Math.PI) / 180);
  const turn = Math.atan2(want.x, want.z) - Math.atan2(sun.x, sun.z);
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromEquirectangular(hdr).texture;
  scene.environmentIntensity = intensity;
  scene.environmentRotation.set(0, turn, 0);
  if (background) {
    scene.background = hdr;
    scene.backgroundRotation.set(0, turn, 0);
  }
  return sun.applyAxisAngle(new THREE.Vector3(0, 1, 0), turn);
}

export async function buildLake(THREE, renderer, scene, shot, files, loadModel, fit) {
  const L = shot.look;
  const sun = await skyLight(THREE, renderer, scene, files["sky.hdr"], { sunAngle: L.sunAngle ?? 80 });
  const key = new THREE.DirectionalLight(0xffd7a8, 3.2);
  key.position.copy(sun.clone().multiplyScalar(6));
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.bias = -0.0004;
  key.shadow.normalBias = 0.02;
  Object.assign(key.shadow.camera, { left: -1.4, right: 1.4, top: 1.4, bottom: -1.4, near: 0.1, far: 20 });
  key.target.position.set(0, 0.8, 0);
  scene.add(key, key.target);
  const fill = new THREE.DirectionalLight(0xffe2c4, 1.6);
  fill.position.set(0.5, 2.5, 4);
  const rim = new THREE.DirectionalLight(0xffb070, 1.2);
  rim.position.set(-3, 1.5, -6);
  scene.add(fill, rim);

  const normals = await loadTex(THREE, files["waternormals.jpg"], false);
  const water = new Water(new THREE.PlaneGeometry(3000, 3000), {
    textureWidth: 1024, textureHeight: 1024, waterNormals: normals, sunDirection: sun.clone(),
    sunColor: 0xffc58a, waterColor: 0x0b2a2e, distortionScale: 2.2, fog: false,
  });
  water.rotation.x = -Math.PI / 2;
  water.material.uniforms.size.value = 3.5;
  scene.add(water);

  const top = shot.ctx.anchors["stage.center"][1];
  if (shot.set.stage !== "none") {
    const [d, n, r] = await Promise.all([
      loadTex(THREE, files["rock/diff.jpg"], true), loadTex(THREE, files["rock/nor.jpg"], false), loadTex(THREE, files["rock/rough.jpg"], false),
    ]);
    for (const t of [d, n, r]) t.repeat.set(1.6, 1);
    const h = top + 0.35;
    const slab = new THREE.Mesh(new RoundedBoxGeometry(1.6, h, 1.0, 8, 0.1),
      new THREE.MeshStandardMaterial({ map: d, normalMap: n, roughnessMap: r, normalScale: new THREE.Vector2(1.6, 1.6) }));
    slab.position.set(0, top - h / 2, 0);
    slab.castShadow = slab.receiveShadow = true;
    scene.add(slab);
  }
  for (const item of shot.env.dressing) {
    const g = fit(await loadModel(item.url), item.dims);
    g.position.set(...item.at);
    g.rotation.y = item.yaw;
    scene.add(g);
  }
  return { sun, tick: (t) => (water.material.uniforms.time.value = t * 0.45) };
}

export async function buildUnderwater(THREE, renderer, scene, shot, files) {
  if (files["sky.hdr"]) await skyLight(THREE, renderer, scene, files["sky.hdr"], { background: false, intensity: 0.35 });
  scene.fog = new THREE.FogExp2(0x0e5a66, 0.16);
  scene.background = new THREE.Color(0x0b4652);
  scene.add(new THREE.HemisphereLight(0x9fe6ff, 0x0c2a20, 1.3));
  const sunU = new THREE.DirectionalLight(0xbff4ff, 1.4);
  sunU.position.set(1, 6, 2);
  scene.add(sunU);
  const sand = await loadTex(THREE, files["sand/diff.jpg"], true);
  const floorMat = new THREE.ShaderMaterial({
    uniforms: { uSand: { value: sand }, uTime: { value: 0 }, fogColor: { value: scene.fog.color }, fogDensity: { value: scene.fog.density } },
    vertexShader: "varying vec2 vUv; varying vec3 vW; void main(){ vUv = uv; vec4 w = modelMatrix * vec4(position,1.0); vW = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }",
    fragmentShader: `uniform sampler2D uSand; uniform float uTime, fogDensity; uniform vec3 fogColor; varying vec2 vUv; varying vec3 vW;
      float caustic(vec2 p){ vec2 i = p; float c = 1.0, inten = 0.005;
        for (int n = 0; n < 5; n++) { float t = uTime * (1.0 - (3.5 / float(n + 1)));
          i = p + vec2(cos(t - i.x) + sin(t + i.y), sin(t - i.y) + cos(t + i.x));
          c += 1.0 / length(vec2(p.x / (sin(i.x + t) / inten), p.y / (cos(i.y + t) / inten))); }
        c /= 5.0; c = 1.17 - pow(c, 1.4); return pow(abs(c), 8.0); }
      void main(){ vec3 s = texture2D(uSand, vUv * 10.0).rgb * vec3(0.55, 0.75, 0.7);
        float k = clamp(caustic(mod(vW.xz * 0.35, 1.0) * 6.2831 - 250.0), 0.0, 1.0);
        vec3 col = s * 0.55 + vec3(0.7, 1.0, 0.95) * k * 0.9;
        float d = length(vW - cameraPosition); float f = 1.0 - exp(-fogDensity * fogDensity * d * d);
        gl_FragColor = vec4(clamp(mix(col, fogColor, f), 0.0, 4.0), 1.0);
        #include <colorspace_fragment>
      }`,
  });
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(80, 80), floorMat);
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = shot.ctx.anchors["underwater.floor"][1] - 0.1;
  scene.add(floor);
  const normals = await loadTex(THREE, files["waternormals.jpg"], false);
  normals.repeat.set(6, 6);
  const surf = new THREE.Mesh(new THREE.PlaneGeometry(80, 80),
    new THREE.MeshBasicMaterial({ color: 0xbff6ff, map: normals, transparent: true, opacity: 0.85, side: THREE.DoubleSide }));
  surf.rotation.x = Math.PI / 2;
  scene.add(surf);
  const rayMat = () => new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, uniforms: { uA: { value: 0.12 } },
    vertexShader: "varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }",
    fragmentShader: "uniform float uA; varying vec2 vUv; void main(){ float a = uA * smoothstep(0.0, 0.5, vUv.x) * smoothstep(1.0, 0.5, vUv.x) * pow(vUv.y, 1.5); gl_FragColor = vec4(vec3(0.75, 1.0, 0.95) * a, a); }",
  });
  const c = shot.ctx.anchors["underwater.center"];
  const rays = Array.from({ length: 7 }, (_, i) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(0.6, 6), rayMat());
    m.position.set(c[0] - 3 + i, -2.8 + 3, c[2] - 1.5 + (i % 3) * 0.9);
    scene.add(m);
    return m;
  });
  const r = mulberry32(11), n = 900, base = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) base.set([c[0] + (r() - 0.5) * 10, -r() * 2.4, c[2] + (r() - 0.5) * 10], i * 3);
  const motes = new THREE.Points(new THREE.BufferGeometry(),
    new THREE.PointsMaterial({ map: softDot(THREE), size: 0.03, transparent: true, opacity: 0.6, depthWrite: false, color: 0xcff8ff }));
  motes.geometry.setAttribute("position", new THREE.BufferAttribute(base.slice(), 3));
  motes.frustumCulled = false;
  scene.add(motes);
  return {
    tick(t, camera) {
      floorMat.uniforms.uTime.value = t * 0.8;
      normals.offset.set(t * 0.03, t * 0.02);
      rays.forEach((ray, i) => {
        ray.material.uniforms.uA.value = 0.1 + 0.05 * Math.sin(t * 1.3 + i);
        ray.lookAt(camera.position.x, ray.position.y, camera.position.z);
        ray.rotateZ(0.25 + 0.04 * Math.sin(t * 0.7 + i * 1.7));
      });
      const a = motes.geometry.attributes.position.array;
      for (let i = 0; i < n; i++) {
        a[i * 3] = base[i * 3] + Math.sin(t * 0.3 + i) * 0.1;
        a[i * 3 + 1] = base[i * 3 + 1] + Math.sin(t * 0.2 + i * 1.3) * 0.08;
      }
      motes.geometry.attributes.position.needsUpdate = true;
    },
  };
}
