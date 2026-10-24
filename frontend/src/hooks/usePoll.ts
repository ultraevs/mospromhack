import { useEffect, useRef } from "react";

export function usePoll(fn: () => void, intervalMs: number, deps: unknown[] = []) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    ref.current();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") ref.current();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs, ...deps]);
}
