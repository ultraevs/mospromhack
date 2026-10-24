import type { SVGProps } from "react";

const paths = {
  overview: "M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z",
  journal: "M6 3h10l3 3v15H6zM9 9h7M9 13h7M9 17h4",
  camera: "M4 7.5h3l1.4-2h7.2l1.4 2h3v10H4v-10Zm4 5a4 4 0 1 0 8 0 4 4 0 0 0-8 0Z",
  analytics: "M4 20V10m6 10V4m6 16v-7m4 7H3",
  gauge: "M4 16a8 8 0 1 1 16 0M12 16l4-5M3 20h18",
  settings: "M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Zm0-12.7v2m0 15v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2.5 12h2m15 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m8-10a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm6-3a3 3 0 0 1 0 6m4 7v-2a4 4 0 0 0-3-3.9",
  logout: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10",
  plus: "M12 5v14m-7-7h14",
  trash: "M5 7h14m-9 4v6m4-6v6M8 7l.7-2h6.6l.7 2m-9 0 .7 13h8.6l.7-13",
  edit: "M4 20h4L19 9l-4-4L4 16v4ZM13 7l4 4",
  close: "M6 6l12 12M18 6 6 18",
  chevron: "m6 9 6 6 6-6",
  check: "m5 12 4 4L19 6",
  refresh: "M20 11a8 8 0 0 0-14.7-3L3 11m0 0h5m-5 0V6m1 7a8 8 0 0 0 14.7 3L21 13m0 0h-5m5 0v5",
  eye: "M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Zm9.5 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z",
  "eye-off": "m3 3 18 18M10.6 6.2A10.8 10.8 0 0 1 12 6c6 0 9.5 6 9.5 6a16.5 16.5 0 0 1-3 3.7M6.2 6.2C3.8 7.7 2.5 12 2.5 12s3.5 6 9.5 6a10.5 10.5 0 0 0 3.2-.5",
  menu: "M4 7h16M4 12h16M4 17h16",
  focus: "M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4",
  wall: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  polygon: "M5 18 4 8l8-5 8 6-2 9z",
  external: "M14 5h5v5m0-5-7 7M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4",
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, size = 18, ...props }: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d={paths[name]} />
    </svg>
  );
}
