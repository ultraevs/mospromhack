import type { SVGProps } from "react";

type IconName =
  | "activity" | "alert" | "arrow" | "camera" | "check" | "chevron" | "clock"
  | "close" | "download" | "external" | "eye" | "eye-off" | "filter" | "grid" | "layout" | "link"
  | "menu" | "monitor" | "play" | "plus" | "refresh" | "search" | "settings"
  | "sliders" | "spark" | "telegram" | "trash" | "users" | "webhook" | "x";

const paths: Record<IconName, string> = {
  activity: "M3 12h4l2.1-7 4 14L15.5 9H21",
  alert: "M10.3 4.2 2.7 17a1.5 1.5 0 0 0 1.3 2.2h16a1.5 1.5 0 0 0 1.3-2.2L13.7 4.2a2 2 0 0 0-3.4 0ZM12 9v3.5m0 3h.01",
  arrow: "M5 12h13m-5-5 5 5-5 5",
  camera: "M4 7.5h3l1.4-2h7.2l1.4 2h3v10H4v-10Zm4 5a4 4 0 1 0 8 0 4 4 0 0 0-8 0Z",
  check: "m5 12 4 4L19 6",
  chevron: "m6 9 6 6 6-6",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-14v5l3 2",
  close: "M6 6l12 12M18 6 6 18",
  download: "M12 3v12m0 0 4-4m-4 4-4-4M5 21h14",
  external: "M14 5h5v5m0-5-7 7M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4",
  eye: "M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Zm9.5 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z",
  "eye-off": "m3 3 18 18M10.6 6.2A10.8 10.8 0 0 1 12 6c6 0 9.5 6 9.5 6a16.5 16.5 0 0 1-3 3.7M6.2 6.2C3.8 7.7 2.5 12 2.5 12s3.5 6 9.5 6a10.5 10.5 0 0 0 3.2-.5",
  filter: "M4 6h16M7 12h10m-7 6h4",
  grid: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
  layout: "M4 4h16v16H4zM4 10h16M10 10v10",
  link: "M10 13a5 5 0 0 0 7.1.1l1.4-1.4a5 5 0 0 0-7.1-7.1L10.6 5.4m3.4 5.2a5 5 0 0 0-7.1-.1l-1.4 1.4a5 5 0 0 0 7.1 7.1l.8-.8",
  menu: "M4 7h16M4 12h16M4 17h16",
  monitor: "M4 5h16v11H4zM9 20h6m-3-4v4",
  play: "m9 6 9 6-9 6V6Z",
  plus: "M12 5v14m-7-7h14",
  refresh: "M20 11a8 8 0 0 0-14.7-3L3 11m0 0h5m-5 0V6m1 7a8 8 0 0 0 14.7 3L21 13m0 0h-5m5 0v5",
  search: "m20 20-4.5-4.5m2-5.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z",
  settings: "M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Zm0-12.7v2m0 15v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2.5 12h2m15 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  sliders: "M4 6h16M4 12h16M4 18h16M8 4v4m8 2v4m-5 6v4",
  spark: "m12 3 1.4 5.6L19 10l-5.6 1.4L12 17l-1.4-5.6L5 10l5.6-1.4L12 3Zm6 14 .5 2 2 .5-2 .5-.5 2-.5-2-2-.5 2-.5.5-2Z",
  telegram: "M21 4 3.5 10.7c-.8.3-.8 1.4 0 1.7l4.4 1.6 1.7 5.2c.2.7 1.1.8 1.5.2l2.5-3.3 4.5 3.3c.6.4 1.4.1 1.5-.6L21.9 5c.1-.7-.4-1.2-.9-1ZM9.1 13.5l8.4-6.1-6.8 7.2-.7 2.4-.9-3.5Z",
  trash: "M5 7h14m-9 4v6m4-6v6M8 7l.7-2h6.6l.7 2m-9 0 .7 13h8.6l.7-13",
  users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m8-10a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm6-3a3 3 0 0 1 0 6m4 7v-2a4 4 0 0 0-3-3.9",
  webhook: "M8 10a4 4 0 1 1 3.5 5.9H8.8m7.2-5.9a4 4 0 1 1-3.5 5.9H16M8.8 10.1 12 4.6l3.2 5.5M8.8 13.9 12 19.4l3.2-5.5",
  x: "M6 6l12 12M18 6 6 18",
};

export function Icon({ name, size = 18, strokeWidth = 1.7, ...props }: { name: IconName; size?: number; strokeWidth?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d={paths[name]} />
    </svg>
  );
}

export type { IconName };
