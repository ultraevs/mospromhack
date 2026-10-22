import { useEffect, useRef, useState } from "react";
import { whepUrl } from "../api";
import { useSocket } from "../hooks/useSocket";
import { videoRect } from "../lib/geometry";
import { DEFAULT_LAYERS, drawDetections, isStale } from "../lib/overlay";
import type { Layers } from "../lib/overlay";
import { connectWhep } from "../lib/whep";
import type { WhepSession } from "../lib/whep";
import type { ClientConfig, DetectionsMessage } from "../types";

type VideoState = "connecting" | "playing" | "error";

interface Props {
  cameraId: number;
  config: ClientConfig;
  layers?: Layers;
  drawExtra?: (ctx: CanvasRenderingContext2D, rect: ReturnType<typeof videoRect>) => void;
  rectRef?: React.MutableRefObject<ReturnType<typeof videoRect> | null>;
  children?: React.ReactNode;
}

export function Player({ cameraId, config, layers = DEFAULT_LAYERS, drawExtra, rectRef, children }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const queue = useRef<DetectionsMessage[]>([]);
  const [state, setState] = useState<VideoState>("connecting");
  const [error, setError] = useState("");

  const boxesConnected = useSocket<DetectionsMessage>(`/ws/detections?camera_id=${cameraId}`, (msg) => {
    if (msg.type !== "detections") return;
    queue.current.push(msg);
    if (queue.current.length > 60) queue.current.shift();
  });

  useEffect(() => {
    let session: WhepSession | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;

    const retry = (reason: string) => {
      if (cancelled) return;
      session?.close();
      session = null;
      setState("error");
      setError(reason);
      timer = setTimeout(start, 2000);
    };

    const start = async () => {
      if (cancelled) return;
      setState("connecting");
      try {
        session = await connectWhep(whepUrl(cameraId, config.webrtc_port), (stream) => {
          // в dev strict mode эффект дважды, старое подключение не должно перебить новое
          if (!cancelled && videoRef.current) videoRef.current.srcObject = stream;
        });
        if (cancelled) return session.close();
        const pc = session.pc;
        pc.onconnectionstatechange = () => {
          if (pc.connectionState === "connected") setState("playing");
          if (pc.connectionState === "failed" || pc.connectionState === "disconnected") retry("связь с видео потеряна");
        };
      } catch (e) {
        retry(e instanceof Error ? e.message : String(e));
      }
    };
    start();

    return () => {
      cancelled = true;
      clearTimeout(timer);
      session?.close();
    };
  }, [cameraId, config.webrtc_port]);

  const layersRef = useRef(layers);
  layersRef.current = layers;
  const extraRef = useRef(drawExtra);
  extraRef.current = drawExtra;

  useEffect(() => {
    let raf = 0;
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
      const q = queue.current;
      let msg: DetectionsMessage | undefined;
      for (let i = q.length - 1; i >= 0; i--) {
        if (now - q[i].ts >= config.overlay_delay_ms) {
          msg = q[i];
          q.splice(0, i);
          break;
        }
      }
      const frame = msg?.frame ?? { w: video.videoWidth, h: video.videoHeight };
      const rect = videoRect(cw, ch, frame.w, frame.h);
      if (rectRef) rectRef.current = rect;
      extraRef.current?.(ctx, rect);
      if (msg && !isStale(msg, now, config.overlay_delay_ms)) drawDetections(ctx, msg, rect, layersRef.current);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [config.overlay_delay_ms, rectRef]);

  return (
    <div className="relative h-full w-full overflow-hidden bg-black">
      <video ref={videoRef} autoPlay muted playsInline className="h-full w-full object-contain" />
      <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full" />
      {children}
      {state !== "playing" && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/60 text-muted">
          {state === "connecting" ? "Подключение к видео…" : `Нет видео: ${error}. Повторяю…`}
        </div>
      )}
      {!boxesConnected && (
        <div className="absolute left-2 top-2 rounded bg-panel/90 px-2 py-1 text-xs text-medium">
          ◆ нет связи с сервером, рамки не обновляются
        </div>
      )}
    </div>
  );
}
