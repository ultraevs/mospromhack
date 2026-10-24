import { useEffect, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import { hlsUrl, whepUrl, wsUrl } from "../api";
import { useClientConfig } from "../hooks/useClientConfig";
import { videoRect, type Rect } from "../lib/geometry";
import { DEFAULT_LAYERS, drawDetections, hasThreat, isStale, takeSynced, type Layers } from "../lib/overlay";
import { connectWhep, type WhepSession } from "../lib/whep";
import type { DetectionsMessage } from "../types";

type VideoState = "connecting" | "playing" | "error";

interface Props {
  cameraId: number;
  overlays?: boolean;
  layers?: Layers;
  drawExtra?: (ctx: CanvasRenderingContext2D, rect: Rect) => void;
  rectRef?: MutableRefObject<Rect | null>;
  children?: ReactNode;
  className?: string;
}

export function LivePlayer({ cameraId, overlays = true, layers = DEFAULT_LAYERS, drawExtra, rectRef, children, className = "" }: Props) {
  const config = useClientConfig();
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const queue = useRef<DetectionsMessage[]>([]);
  const latencyMs = useRef(0);
  const [state, setState] = useState<VideoState>("connecting");
  const [error, setError] = useState("");
  const [threat, setThreat] = useState(false);
  const [boxesLost, setBoxesLost] = useState(false);

  const layersRef = useRef(layers);
  layersRef.current = layers;
  const extraRef = useRef(drawExtra);
  extraRef.current = drawExtra;

  useEffect(() => {
    if (!config) return;
    const video = videoRef.current;
    if (!video) return;
    let cancelled = false;
    let session: WhepSession | null = null;
    let hls: import("hls.js").default | null = null;
    let retryTimer: number | undefined;
    let latencyTimer: number | undefined;
    latencyMs.current = 0;

    const cleanup = () => {
      session?.close();
      session = null;
      hls?.destroy();
      hls = null;
      window.clearInterval(latencyTimer);
      video.removeAttribute("src");
      video.srcObject = null;
    };
    const retry = (message: string) => {
      if (cancelled) return;
      cleanup();
      setState("error");
      setError(message);
      retryTimer = window.setTimeout(start, 2500);
    };
    const start = async () => {
      if (cancelled) return;
      setState("connecting");
      video.onplaying = () => setState("playing");
      try {
        if (config.video === "hls") {
          const url = hlsUrl(cameraId, config.hls_port);
          const { default: Hls } = await import("hls.js");
          if (cancelled) return;
          if (Hls.isSupported()) {
            hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 2, liveMaxLatencyDurationCount: 5, maxLiveSyncPlaybackRate: 1.2, backBufferLength: 10 });
            hls.on(Hls.Events.ERROR, (_e, data) => {
              if (data.fatal) retry("поток HLS недоступен");
            });
            hls.loadSource(url);
            hls.attachMedia(video);
            void video.play().catch(() => undefined);
            latencyTimer = window.setInterval(() => {
              const lat = hls?.latency;
              if (lat && Number.isFinite(lat)) latencyMs.current = lat * 1000;
            }, 500);
          } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
            video.src = url;
            void video.play().catch(() => undefined);
            latencyTimer = window.setInterval(() => {
              const r = video.seekable;
              if (r.length) latencyMs.current = Math.max(0, r.end(r.length - 1) - video.currentTime) * 1000;
            }, 500);
          } else {
            throw new Error("браузер не умеет HLS");
          }
          return;
        }
        session = await connectWhep(whepUrl(cameraId, config.webrtc_port), (stream) => {
          // в dev strict mode эффект дважды, старое подключение не должно перебить новое
          if (cancelled) return;
          video.srcObject = stream;
          void video.play().catch(() => undefined);
        });
        if (cancelled) return session.close();
        const pc = session.pc;
        pc.onconnectionstatechange = () => {
          if (pc.connectionState === "failed" || pc.connectionState === "disconnected") retry("связь с видео потеряна");
        };
      } catch (e) {
        retry(e instanceof Error ? e.message : "поток недоступен");
      }
    };
    void start();
    return () => {
      cancelled = true;
      window.clearTimeout(retryTimer);
      cleanup();
    };
  }, [cameraId, config]);

  useEffect(() => {
    if (!overlays) return;
    let socket: WebSocket | null = null;
    let timer: number | undefined;
    let closed = false;
    const open = () => {
      socket = new WebSocket(wsUrl(`/ws/detections?camera_id=${cameraId}`));
      socket.onopen = () => setBoxesLost(false);
      socket.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as DetectionsMessage;
          if (msg.type !== "detections") return;
          queue.current.push(msg);
          if (queue.current.length > 300) queue.current.shift();
        } catch {
          // битый кадр рамок не должен ронять видео
        }
      };
      socket.onclose = () => {
        if (closed) return;
        setBoxesLost(true);
        timer = window.setTimeout(open, 3000);
      };
    };
    open();
    return () => {
      closed = true;
      window.clearTimeout(timer);
      socket?.close();
      queue.current = [];
    };
  }, [cameraId, overlays]);

  const hasExtra = drawExtra !== undefined;
  useEffect(() => {
    if (!overlays && !hasExtra) return;
    let raf = 0;
    let lastThreat = false;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const canvas = canvasRef.current;
      const video = videoRef.current;
      if (!canvas || !video) return;
      const dpr = window.devicePixelRatio || 1;
      const cw = canvas.clientWidth;
      const ch = canvas.clientHeight;
      if (canvas.width !== Math.round(cw * dpr) || canvas.height !== Math.round(ch * dpr)) {
        canvas.width = Math.round(cw * dpr);
        canvas.height = Math.round(ch * dpr);
      }
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cw, ch);
      const now = Date.now();
      const delay = (config?.overlay_delay_ms ?? 0) + latencyMs.current;
      const msg = overlays ? takeSynced(queue.current, now, delay) : undefined;
      const live = msg && !isStale(msg, now, delay) ? msg : undefined;
      const frame = live?.frame ?? { w: video.videoWidth, h: video.videoHeight };
      const rect = videoRect(cw, ch, frame.w, frame.h);
      if (rectRef) rectRef.current = rect;
      extraRef.current?.(ctx, rect);
      if (live) drawDetections(ctx, live, rect, layersRef.current);
      const nextThreat = live ? hasThreat(live) : false;
      if (nextThreat !== lastThreat) {
        lastThreat = nextThreat;
        setThreat(nextThreat);
      }
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [overlays, config, rectRef, hasExtra]);

  return (
    <div className={`player ${threat ? "is-threat" : ""} ${className}`}>
      <video ref={videoRef} autoPlay muted playsInline />
      <canvas ref={canvasRef} aria-hidden="true" />
      {children}
      {state !== "playing" && (
        <div className="player-state">{state === "connecting" ? "Подключаемся к видео…" : `Нет видео: ${error}. Пробуем снова…`}</div>
      )}
      {overlays && boxesLost && state === "playing" && <div className="player-flag">Нет связи с сервером, рамки не обновляются</div>}
    </div>
  );
}
