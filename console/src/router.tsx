import {
  createBrowserRouter,
  createMemoryRouter,
  type RouteObject,
} from "react-router-dom";

import type { ProducerApi } from "./api/client";
import { DownloadScreen } from "./screens/DownloadScreen";
import { EmptyProject } from "./screens/EmptyProject";
import { ProjectsList } from "./screens/ProjectsList";
import { RunCard } from "./screens/RunCard";
import { LocKitWizard } from "./screens/wizard/LocKitWizard";

function routes(api: ProducerApi): RouteObject[] {
  return [
    { path: "/", element: <ProjectsList api={api} /> },
    { path: "/projects", element: <ProjectsList api={api} /> },
    { path: "/projects/:slug", element: <EmptyProject api={api} /> },
    { path: "/projects/:slug/localize", element: <LocKitWizard api={api} /> },
    { path: "/projects/:slug/runs/:runId", element: <RunCard api={api} /> },
    { path: "/projects/:slug/download", element: <DownloadScreen api={api} /> },
  ];
}

export function createConsoleRouter(api: ProducerApi, consoleRoot: string) {
  const basename = consoleRoot === "/" ? "/" : consoleRoot.replace(/\/$/, "");
  return createBrowserRouter(routes(api), { basename });
}

export function createConsoleMemoryRouter(
  api: ProducerApi,
  initialEntries: string[],
) {
  return createMemoryRouter(routes(api), { initialEntries });
}
