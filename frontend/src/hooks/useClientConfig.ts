import { useEffect, useState } from "react";
import { api } from "../api";
import type { ClientConfig } from "../types";

let cached: Promise<ClientConfig> | null = null;

export function useClientConfig(): ClientConfig | null {
  const [config, setConfig] = useState<ClientConfig | null>(null);
  useEffect(() => {
    let alive = true;
    cached ??= api.clientConfig().catch((e) => {
      cached = null;
      throw e;
    });
    cached.then((c) => alive && setConfig(c)).catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  return config;
}
