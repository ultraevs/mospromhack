import type { Route } from "../types-route";
import type { UserRole } from "../types";

export interface PageProps {
  refreshKey: number;
  navigate: (route: Route) => void;
  userRole: UserRole;
}
