// минимальный whep клиент, тянет видео из mediamtx по webrtc
export interface WhepSession {
  pc: RTCPeerConnection;
  close: () => void;
}

function waitIceGathering(pc: RTCPeerConnection, timeoutMs: number): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      pc.removeEventListener("icegatheringstatechange", check);
      resolve();
    };
    const check = () => pc.iceGatheringState === "complete" && done();
    pc.addEventListener("icegatheringstatechange", check);
    setTimeout(done, timeoutMs);
  });
}

export async function connectWhep(url: string, onStream: (stream: MediaStream) => void): Promise<WhepSession> {
  const pc = new RTCPeerConnection();
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.ontrack = (e) => onStream(e.streams[0] ?? new MediaStream([e.track]));

  let resource: string | null = null;
  const close = () => {
    pc.close();
    if (resource) fetch(resource, { method: "DELETE" }).catch(() => undefined);
  };

  try {
    await pc.setLocalDescription(await pc.createOffer());
    await waitIceGathering(pc, 1000);
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription!.sdp,
    });
    if (!res.ok) throw new Error(res.status === 404 ? "поток не публикуется" : `MediaMTX ответил ${res.status}`);
    const location = res.headers.get("Location");
    if (location) resource = new URL(location, url).toString();
    await pc.setRemoteDescription({ type: "answer", sdp: await res.text() });
  } catch (e) {
    close();
    throw e;
  }
  return { pc, close };
}
