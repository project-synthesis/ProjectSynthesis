<!--
  HelixMark.svelte — DNA double helix brand mark
  Canvas 2D parametric renderer with organic animations, 3D depth simulation,
  and energy packet telemetry. Ported from helix-v6 reference.
  See: .superpowers/brainstorm/2581704-1773287459/helix-v6.html
-->
<script module lang="ts">
  // ── Module-level: shared across all HelixMark instances ──

  // Color constants (brand palette)
  const CYAN_RGB = '0, 229, 255';
  const PURPLE_RGB = '168, 85, 247';

  // Pre-parsed RGB channels for flash-cooling interpolation (avoids per-frame parseInt)
  const CYAN_CHANNELS = [0, 229, 255];
  const PURPLE_CHANNELS = [168, 85, 247];

  function getChannels(colorRGB: string): [number, number, number] {
    return colorRGB === CYAN_RGB ? CYAN_CHANNELS as [number, number, number]
                                 : PURPLE_CHANNELS as [number, number, number];
  }

  interface HelixPoint {
    x: number;
    y: number;
    zDepth: number;
    isTarget: boolean;
  }

  interface RibbonChunk {
    pts: HelixPoint[];
    colorRGB: string;
    channels: [number, number, number];
    z: number;
    isGuideWire: boolean;
  }

  interface HelixOpts {
    turns: number;
    amplitude: number;
    rungCount: number;
    strandW: number;
    rungW: number;
    tailLen: number;
    speed: number;
    rotation: number;
    samples: number;
    instanceId: number;
    buildDur: number;
    buildDelay: number;
    loopBuild: boolean;
  }

  // Adaptive defaults by size (from v6 reference instances)
  interface SizePreset {
    amplitude: number;
    rungCount: number;
    strandW: number;
    rungW: number;
    tailLen: number;
  }

  const SIZE_PRESETS: { max: number; preset: SizePreset }[] = [
    { max: 16, preset: { amplitude: 0.16, rungCount: 0, strandW: 0.065, rungW: 0,     tailLen: 0.08 } },
    { max: 20, preset: { amplitude: 0.15, rungCount: 3, strandW: 0.050, rungW: 0.020, tailLen: 0.10 } },
    { max: 32, preset: { amplitude: 0.14, rungCount: 3, strandW: 0.036, rungW: 0.015, tailLen: 0.11 } },
    { max: 48, preset: { amplitude: 0.13, rungCount: 5, strandW: 0.026, rungW: 0.011, tailLen: 0.12 } },
    { max: 56, preset: { amplitude: 0.12, rungCount: 6, strandW: 0.022, rungW: 0.009, tailLen: 0.13 } },
  ];

  const DEFAULT_PRESET: SizePreset = { amplitude: 0.12, rungCount: 8, strandW: 0.020, rungW: 0.009, tailLen: 0.13 };

  function getPresetForSize(sz: number): SizePreset {
    for (const { max, preset } of SIZE_PRESETS) {
      if (sz <= max) return preset;
    }
    return DEFAULT_PRESET;
  }

  // ── Rendering engine (ported verbatim from v6) ──

  /** Sum-of-sines noise for fluid organic motion. */
  function synthNoise(t: number, freq1 = 1.0, freq2 = 1.618, freq3 = 2.718): number {
    return (Math.sin(t * freq1) + Math.sin(t * freq2) + Math.sin(t * freq3)) / 3.0;
  }

  function drawHelix(canvas: HTMLCanvasElement, opts: HelixOpts, timeSec: number): void {
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    const {
      turns, amplitude, rungCount, strandW: swFrac, rungW: rwFrac,
      rotation, samples, tailLen, speed, instanceId,
      buildDur, buildDelay, loopBuild
    } = opts;

    // Build progress with smoothstep easing
    let buildProgress = 1.0;
    if (buildDur > 0) {
      let elapsed = timeSec - buildDelay;
      if (loopBuild) {
        const cycle = buildDur + 2.0;
        elapsed = ((elapsed % cycle) + cycle) % cycle;
      } else {
        elapsed = Math.max(0, elapsed);
      }
      const rawP = Math.min(1.0, Math.max(0, elapsed / buildDur));
      buildProgress = rawP * rawP * (3.0 - 2.0 * rawP);
    }

    const cx = W / 2;
    const cy = H / 2;
    const A = W * amplitude;
    const sw = Math.max(W * swFrac, 1);
    const rw = Math.max(W * rwFrac, 0.5);
    const tail = W * tailLen;
    const flowPhase = timeSec * speed * Math.PI;
    const theta = rotation * Math.PI / 180;
    const sinT = Math.sin(theta);
    const cosT = Math.cos(theta);

    // Bounding box for 45° rotation
    const maxA = A * 1.5;
    const pad = sw * 3;
    const maxTotalFromW = (W - pad - 2 * maxA * cosT) / sinT;
    const maxTotalFromH = (H - pad - 2 * maxA * sinT) / cosT;
    const totalH = Math.min(maxTotalFromW, maxTotalFromH);
    const coreSpan = totalH - 2 * tail;

    if (coreSpan <= 0) {
      drawOrganicFilament(ctx, W, H, cx, cy, A, sw, rw, theta, turns, samples, totalH, 0, rungCount, flowPhase, timeSec, buildProgress, instanceId);
    } else {
      drawOrganicFilament(ctx, W, H, cx, cy, A, sw, rw, theta, turns, samples, coreSpan, tail, rungCount, flowPhase, timeSec, buildProgress, instanceId);
    }
  }

  function drawOrganicFilament(
    ctx: CanvasRenderingContext2D,
    W: number, H: number, cx: number, cy: number,
    A: number, sw: number, rw: number, theta: number,
    turns: number, samples: number, coreSpan: number, tail: number,
    rungCount: number, flowPhase: number, timeSec: number,
    buildProgress: number, instanceId: number
  ): void {
    const yCoreTop = cy - coreSpan / 2;
    const yCoreBot = cy + coreSpan / 2;
    const totalTop = tail > 0 ? yCoreTop - tail : yCoreTop;
    const totalBot = tail > 0 ? yCoreBot + tail : yCoreBot;
    const currentYThreshold = totalBot - (totalBot - totalTop) * buildProgress;
    const omega = turns * 2 * Math.PI / coreSpan;

    function getDisplacement(y: number, time: number) {
      const normY = (y - totalTop) / (totalBot - totalTop);
      const swayX = A * 0.4 * synthNoise(time * 0.5 + normY * 2.0 + instanceId);
      const breatheAmp = 1.0 + 0.3 * synthNoise(time * 0.8 - normY * 3.0 + instanceId * 2.1);
      return { swayX, breatheAmp };
    }

    function buildFilament(phaseOffset: number) {
      const pts: HelixPoint[] = [];
      const startY = tail > 0 ? totalTop : yCoreTop;
      const endY = tail > 0 ? totalBot : yCoreBot;
      const totalDist = endY - startY;
      const denseSamples = samples * 1.5;

      for (let i = 0; i <= denseSamples; i++) {
        const y = startY + totalDist * (i / denseSamples);
        const disp = getDisplacement(y, timeSec);
        const thetaRaw = omega * (y - yCoreTop) + phaseOffset - flowPhase;
        const zDepth = Math.cos(thetaRaw);
        const localAmplitude = A * disp.breatheAmp;
        const x = cx + disp.swayX + localAmplitude * Math.sin(thetaRaw);
        pts.push({ x, y, zDepth, isTarget: false });
      }

      const guideWire: HelixPoint[] = [];
      const built: HelixPoint[] = [];
      const targetThreshold = (totalDist / denseSamples) * 2.0;

      for (const p of pts) {
        if (p.y < currentYThreshold) {
          guideWire.push(p);
        } else {
          built.push({ ...p, isTarget: Math.abs(p.y - currentYThreshold) < targetThreshold });
        }
      }

      if (built.length > 0 && guideWire.length > 0) {
        guideWire.push(built[0]);
      }

      return { guideWire, built, allPts: pts };
    }

    const str1 = buildFilament(0);
    const str2 = buildFilament(Math.PI);

    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(theta);
    ctx.translate(-cx, -cy);

    ctx.lineJoin = 'round';
    ctx.lineCap = 'butt';

    function getWidthForZ(zDepth: number, baseSw: number): number {
      const depthFactor = (zDepth + 1.0) / 2.0;
      return Math.max(1.0, baseSw * (0.15 + 0.85 * depthFactor));
    }

    // --- 1. Synaptic Beziers (Connecting Rungs) ---
    if (rungCount > 0) {
      for (let i = 0; i < rungCount; i++) {
        const frac = (i + 0.5) / rungCount;
        const y = yCoreTop + coreSpan * frac;
        if (y < currentYThreshold) continue;

        const distancePastSynthesis = y - currentYThreshold;
        const disp = getDisplacement(y, timeSec);
        const thetaRaw1 = omega * (y - yCoreTop) - flowPhase;
        const thetaRaw2 = thetaRaw1 + Math.PI;
        const zDepth1 = Math.cos(thetaRaw1);
        const zDepth2 = Math.cos(thetaRaw2);
        const avgZ = (zDepth1 + zDepth2) / 2;
        const localAmplitude = A * disp.breatheAmp;
        const x1 = cx + disp.swayX + localAmplitude * Math.sin(thetaRaw1);
        const x2 = cx + disp.swayX + localAmplitude * Math.sin(thetaRaw2);
        const gap = Math.abs(x2 - x1);
        if (gap < sw * 1.5) continue;

        const dx = x2 - x1;
        const inset = sw * 0.4;
        if (Math.abs(dx) < inset * 2) continue;

        const rx1 = x1 + inset * Math.sign(dx);
        const rx2 = x2 - inset * Math.sign(dx);

        ctx.beginPath();
        ctx.moveTo(rx1, y);
        const droopAmount = (avgZ * 0.5 + 0.5) * A * 0.6;
        ctx.bezierCurveTo(
          rx1 + (rx2 - rx1) * 0.25, y + droopAmount,
          rx1 + (rx2 - rx1) * 0.75, y + droopAmount,
          rx2, y
        );
        ctx.lineWidth = Math.max(0.5, rw * (0.4 + 0.6 * (avgZ * 0.5 + 0.5)));

        const flashLimit = A * 1.2;
        if (distancePastSynthesis < flashLimit && buildProgress < 1.0) {
          const p = distancePastSynthesis / flashLimit;
          ctx.strokeStyle = `rgba(255, ${255 * (1 - p) + 58 * p}, ${255 * (1 - p) + 237 * p}, 1.0)`;
          ctx.lineWidth *= (1.0 + 0.8 * (1.0 - p));
        } else {
          ctx.strokeStyle = `rgba(124, 58, 237, 0.85)`;
        }
        ctx.stroke();
      }
    }

    // --- 2. Depth-sorted ribbon rendering ---
    const ribbonChunks: RibbonChunk[] = [];
    const chunkLen = 2;

    function sliceStrandIntoChunks(pts: HelixPoint[], colorRGB: string, isGuideWire: boolean): void {
      const channels = getChannels(colorRGB);
      for (let i = 0; i < pts.length - chunkLen; i += chunkLen) {
        const chunkPts = pts.slice(i, i + chunkLen + 1);
        const avgZ = chunkPts.reduce((sum, p) => sum + p.zDepth, 0) / chunkPts.length;
        ribbonChunks.push({ pts: chunkPts, colorRGB, channels, z: avgZ, isGuideWire });
      }
    }

    sliceStrandIntoChunks(str1.guideWire, CYAN_RGB, true);
    sliceStrandIntoChunks(str1.built, CYAN_RGB, false);
    sliceStrandIntoChunks(str2.guideWire, PURPLE_RGB, true);
    sliceStrandIntoChunks(str2.built, PURPLE_RGB, false);

    ribbonChunks.sort((a, b) => a.z - b.z);

    for (const chunk of ribbonChunks) {
      if (chunk.pts.length < 2) continue;
      ctx.beginPath();
      ctx.moveTo(chunk.pts[0].x, chunk.pts[0].y);
      for (let i = 1; i < chunk.pts.length; i++) {
        if (i < chunk.pts.length - 1) {
          const xm = (chunk.pts[i].x + chunk.pts[i + 1].x) / 2;
          const ym = (chunk.pts[i].y + chunk.pts[i + 1].y) / 2;
          ctx.quadraticCurveTo(chunk.pts[i].x, chunk.pts[i].y, xm, ym);
        } else {
          ctx.lineTo(chunk.pts[i].x, chunk.pts[i].y);
        }
      }

      if (chunk.isGuideWire) {
        ctx.lineWidth = sw * 0.15;
        ctx.strokeStyle = `rgba(${chunk.colorRGB}, 0.25)`;
        ctx.stroke();
      } else {
        const localThickness = getWidthForZ(chunk.z, sw);
        ctx.lineWidth = localThickness;
        const distFromSynth = chunk.pts[0].y - currentYThreshold;
        if (distFromSynth < A * 1.5 && buildProgress < 1.0) {
          const p = Math.max(0, distFromSynth / (A * 1.5));
          const [, g, b] = chunk.channels;
          ctx.strokeStyle = `rgba(255, ${255 * (1 - p) + g * p}, ${255 * (1 - p) + b * p}, 1.0)`;
          ctx.lineWidth = localThickness * (1.0 + 0.6 * (1.0 - p));
        } else {
          ctx.strokeStyle = `rgba(${chunk.colorRGB}, 1.0)`;
        }
        ctx.stroke();
      }
    }

    // --- 3. Fluid Teardrop Telemetry ---
    function drawFluidTelemetry(allPts: HelixPoint[], speedOffset: number): void {
      if (allPts.length < 2 || sw <= 1.5) return;
      const path = new Path2D();
      path.moveTo(allPts[0].x, allPts[0].y);
      for (let i = 1; i < allPts.length; i++) {
        if (i < allPts.length - 1) {
          const xm = (allPts[i].x + allPts[i + 1].x) / 2;
          const ym = (allPts[i].y + allPts[i + 1].y) / 2;
          path.quadraticCurveTo(allPts[i].x, allPts[i].y, xm, ym);
        } else {
          path.lineTo(allPts[i].x, allPts[i].y);
        }
      }

      ctx.save();
      ctx.setLineDash([W * 0.05, W * 0.02, W * 0.01, W * 0.005, W * 0.3]);
      ctx.lineDashOffset = (timeSec * 60) + speedOffset;
      const packGrad = ctx.createLinearGradient(0, currentYThreshold, 0, totalBot);
      packGrad.addColorStop(0, 'rgba(255, 255, 255, 1.0)');
      packGrad.addColorStop(0.5, 'rgba(255, 255, 255, 0.4)');
      packGrad.addColorStop(1, 'rgba(255, 255, 255, 0.0)');
      ctx.strokeStyle = packGrad;
      ctx.lineWidth = sw * 0.5;
      ctx.stroke(path);
      ctx.restore();
    }

    drawFluidTelemetry(str1.built, 0);
    drawFluidTelemetry(str2.built, W * 0.15);

    // --- 4. Synthesis Glide Nodes ---
    if (buildProgress > 0.0 && buildProgress < 1.0) {
      function drawGlideNode(strPts: HelixPoint[], colorRGB: string): void {
        const nodePt = strPts.find(p => p.isTarget);
        if (!nodePt) return;

        const nodeScale = getWidthForZ(nodePt.zDepth, sw) * 2.2;
        ctx.save();
        ctx.translate(nodePt.x, nodePt.y);
        ctx.rotate(Math.PI / 2);

        ctx.beginPath();
        ctx.moveTo(0, -nodeScale * 1.8);
        ctx.lineTo(nodeScale * 0.8, 0);
        ctx.lineTo(0, nodeScale * 0.5);
        ctx.lineTo(-nodeScale * 0.8, 0);
        ctx.closePath();

        ctx.fillStyle = '#ffffff';
        ctx.fill();
        ctx.lineWidth = 1.0;
        ctx.strokeStyle = `rgba(${colorRGB}, 1.0)`;
        ctx.stroke();
        ctx.restore();
      }

      drawGlideNode(str1.built, CYAN_RGB);
      drawGlideNode(str2.built, PURPLE_RGB);

      // Organic capillary connection between synthesis cursors
      const node1 = str1.built.find(p => p.isTarget);
      const node2 = str2.built.find(p => p.isTarget);

      if (node1 && node2 && sw > 1.5) {
        const nodeGap = Math.abs(node2.x - node1.x);
        if (nodeGap > sw * 2.0) {
          ctx.beginPath();
          ctx.moveTo(node1.x, node1.y);
          ctx.bezierCurveTo(
            node1.x, node1.y + A * 0.3,
            node2.x, node2.y + A * 0.3,
            node2.x, node2.y
          );
          ctx.lineWidth = sw * 0.5;
          ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
          ctx.stroke();
        }
      }
    }

    ctx.restore();
  }
</script>

<script lang="ts">
  import { onMount } from 'svelte';

  // --- Props ---
  interface Props {
    size?: number;
    turns?: number;
    amplitude?: number;
    rungCount?: number;
    strandW?: number;
    rungW?: number;
    tailLen?: number;
    speed?: number;
    opacity?: number;
    buildDur?: number;
    buildDelay?: number;
    loopBuild?: boolean;
    instanceId?: number;
  }

  let {
    size = 32,
    turns = 1.5,
    amplitude,
    rungCount,
    strandW,
    rungW,
    tailLen,
    speed = -0.5,
    opacity = 1.0,
    buildDur,
    buildDelay = 0,
    loopBuild = false,
    instanceId = 0
  }: Props = $props();

  // Resolve final values: explicit prop > size-adaptive default
  let resolvedOpts = $derived.by((): HelixOpts => {
    const d = getPresetForSize(size);
    return {
      turns,
      amplitude: amplitude ?? d.amplitude,
      rungCount: rungCount ?? d.rungCount,
      strandW: strandW ?? d.strandW,
      rungW: rungW ?? d.rungW,
      tailLen: tailLen ?? d.tailLen,
      speed,
      rotation: 45,
      samples: 200,
      instanceId,
      buildDur: buildDur ?? 0,
      buildDelay,
      loopBuild,
    };
  });

  // --- Lifecycle ---
  let canvasEl: HTMLCanvasElement;

  onMount(() => {
    let rafId: number;
    let startTime: number | null = null;

    function renderLoop(time: number) {
      if (!startTime) startTime = time;
      const globalSecBase = (time - startTime) * 0.001;
      const surge = 0.2 * synthNoise(globalSecBase * 0.5);
      const globalSec = globalSecBase + surge;

      if (canvasEl) {
        drawHelix(canvasEl, resolvedOpts, globalSec);
      }
      rafId = requestAnimationFrame(renderLoop);
    }

    rafId = requestAnimationFrame(renderLoop);

    return () => {
      cancelAnimationFrame(rafId);
    };
  });
</script>

<canvas
  bind:this={canvasEl}
  width={size * 2}
  height={size * 2}
  style="width: {size}px; height: {size}px; opacity: {opacity}; display: block;"
></canvas>
