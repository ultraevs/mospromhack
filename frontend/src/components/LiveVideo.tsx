import { useEffect, useRef, useState } from "react";
import { api, hlsUrl, whepUrl, wsUrl } from "../api";
import { connectWhep, type WhepSession } from "../lib/whep";
import { drawVideoOverlay, fitVideoRect, hasThreat } from "../lib/videoOverlay";
import type { ClientConfig, DetectionsMessage, Zone } from "../types";

type VideoState = "connecting" | "playing" | "error";

export function LiveVideo({ cameraId, className = "", zones = [], overlays = true }: { cameraId: number; className?: string; zones?: Zone[]; overlays?: boolean }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const detectionRef = useRef<DetectionsMessage | null>(null);
  const zonesRef = useRef(zones);
  zonesRef.current = zones;
  const [state, setState] = useState<VideoState>("connecting");
  const [error, setError] = useState("Подключение к видео…");
  const [threat, setThreat] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let session: WhepSession | null = null;
    let hls: import("hls.js").default | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    const cleanup = () => {
      session?.close();
      session = null;
      hls?.destroy();
      hls = null;
      if (videoRef.current) {
        videoRef.current.pause();
        videoRef.current.removeAttribute("src");
        videoRef.current.srcObject = null;
        videoRef.current.load();
      }
    };

    const retry = (message: string) => {
      if (cancelled) return;
      cleanup();
      setState("error");
      setError(message);
      retryTimer = setTimeout(start, 2500);
    };

    const start = async () => {
      if (cancelled) return;
      setState("connecting");
      setError("Подключение к видео…");
      try {
        const config: ClientConfig = await api.clientConfig();
        const video = videoRef.current;
        if (!video) throw new Error("Видеоэлемент не готов");
        video.onplaying = () => setState("playing");

        if (config.video === "hls") {
          const url = hlsUrl(cameraId, config.hls_port);
          const { default: Hls } = await import("hls.js");
          if (Hls.isSupported()) {
            hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 2, liveMaxLatencyDurationCount: 5, backBufferLength: 10 });
            hls.on(Hls.Events.ERROR, (_event, data) => {
              if (data.fatal) retry(data.details || "HLS-поток недоступен");
            });
            hls.loadSource(url);
            hls.attachMedia(video);
          } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
            video.src = url;
            void video.play().catch(() => undefined);
          } else {
            throw new Error("Браузер не поддерживает WebRTC/HLS");
          }
          return;
        }

        session = await connectWhep(whepUrl(cameraId, config.webrtc_port), (stream) => {
          if (cancelled || !videoRef.current) return;
          videoRef.current.srcObject = stream;
          void videoRef.current.play().catch(() => undefined);
        });
        if (cancelled) return session.close();
        session.pc.onconnectionstatechange = () => {
          if (session?.pc.connectionState === "connected") setState("playing");
          if (session?.pc.connectionState === "failed" || session?.pc.connectionState === "disconnected") retry("Связь с видео потеряна");
        };
      } catch (reason) {
        retry(reason instanceof Error ? reason.message : "Поток недоступен");
      }
    };

    void start();
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      cleanup();
    };
  }, [cameraId]);

  useEffect(() => {
    if (!overlays) return;
    let cancelled = false;
    const socket = new WebSocket(wsUrl(`/ws/detections?camera_id=${cameraId}`));
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as DetectionsMessage;
        if (message.type !== "detections") return;
        detectionRef.current = message;
        const nextThreat = hasThreat(message);
        setThreat((current) => current === nextThreat ? current : nextThreat);
      } catch {
        // A malformed detection frame should not interrupt the camera stream.
      }
    };
    const draw = () => {
      if (cancelled) return;
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (video && canvas) {
        const dpr = window.devicePixelRatio || 1;
        const width = canvas.clientWidth;
        const height = canvas.clientHeight;
        if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
          canvas.width = Math.round(width * dpr);
          canvas.height = Math.round(height * dpr);
        }
        const ctx = canvas.getContext("2d");
        if (ctx) {
          ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
          const frame = detectionRef.current?.frame ?? { w: video.videoWidth, h: video.videoHeight };
          drawVideoOverlay(ctx, detectionRef.current, zonesRef.current, fitVideoRect(width, height, frame.w, frame.h));
        }
      }
      requestAnimationFrame(draw);
    };
    const frame = requestAnimationFrame(draw);
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      socket.close();
      detectionRef.current = null;
      setThreat(false);
    };
  }, [cameraId, overlays]);

  return (
    <div className={`live-video-shell ${threat ? "is-threat" : ""} ${className}`} aria-label={`Видео камеры ${cameraId}`}>
      <video ref={videoRef} className="live-video" autoPlay muted playsInline aria-label={`Live-видео камеры ${cameraId}`} />
      {overlays && <canvas ref={canvasRef} className="live-video-overlay-canvas" aria-hidden="true" />}
      {state !== "playing" && <div className="live-video-overlay"><span className="status-dot" />{state === "connecting" ? "Подключение к видео…" : error}</div>}
      {threat && <div className="threat-status" role="status"><span className="threat-status-dot" />Есть угроза в кадре</div>}
    </div>
  );
}
