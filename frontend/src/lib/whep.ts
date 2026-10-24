export interface WhepSession {
  pc: RTCPeerConnection;
  close: () => void;
}

function waitIceGathering(pc: RTCPeerConnection, timeoutMs: number): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const done = () => {
      if (timer) clearTimeout(timer);
      pc.removeEventListener("icegatheringstatechange", check);
      resolve();
    };
    const check = () => { if (pc.iceGatheringState === "complete") done(); };
    timer = setTimeout(done, timeoutMs);
    pc.addEventListener("icegatheringstatechange", check);
  });
}

export async function connectWhep(url: string, onStream: (stream: MediaStream) => void): Promise<WhepSession> {
  const pc = new RTCPeerConnection();
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.ontrack = (event) => onStream(event.streams[0] ?? new MediaStream([event.track]));
  const close = () => pc.close();

  try {
    await pc.setLocalDescription(await pc.createOffer());
    await waitIceGathering(pc, 1200);
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription?.sdp ?? "",
    });
    if (!response.ok) throw new Error(response.status === 404 ? "поток не публикуется" : `MediaMTX ответил ${response.status}`);
    await pc.setRemoteDescription({ type: "answer", sdp: await response.text() });
  } catch (error) {
    close();
    throw error;
  }
  return { pc, close };
}
