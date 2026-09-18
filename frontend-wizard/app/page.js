"use client";

import * as React from "react";
import { AdvancedPlaceholderScreen } from "@/components/console/screens/AdvancedPlaceholder";
import { DecisionsQueueScreen } from "@/components/console/screens/DecisionsQueue";
import { DownloadScreen } from "@/components/console/screens/DownloadScreen";
import { EmailPreviewScreen } from "@/components/console/screens/EmailPreview";
import { EmptyProjectScreen } from "@/components/console/screens/EmptyProject";
import { GlossaryScreen } from "@/components/console/screens/GlossaryScreen";
import { ProjectOverviewScreen } from "@/components/console/screens/ProjectOverview";
import { ProjectsListScreen } from "@/components/console/screens/ProjectsList";
import { RunCardScreen } from "@/components/console/screens/RunCard";
import { SettingsScreen } from "@/components/console/screens/SettingsScreen";
import { StatesCatalogueScreen } from "@/components/console/screens/StatesCatalogue";
import { UniversalWizardScreen } from "@/components/console/screens/UniversalWizard";
import { UploadScreen } from "@/components/console/screens/UploadScreen";
import { WizardScreen } from "@/components/console/screens/Wizard";
import { Skeleton } from "@/components/ui/skeleton";
import { Toaster } from "@/components/ui/sonner";
import * as api from "@/src/api/client";

function useLocation() {
  const [loc, setLoc] = React.useState(() => ({ pathname: "/", search: "" }));
  React.useEffect(() => {
    const update = () =>
      setLoc({
        pathname: window.location.pathname,
        search: window.location.search,
      });
    update();
    window.addEventListener("popstate", update);
    window.addEventListener("hcgl:navigate", update);
    return () => {
      window.removeEventListener("popstate", update);
      window.removeEventListener("hcgl:navigate", update);
    };
  }, []);
  const navigate = React.useCallback((to) => {
    window.history.pushState({}, "", to);
    window.dispatchEvent(new Event("hcgl:navigate"));
    window.scrollTo(0, 0);
  }, []);
  return { ...loc, navigate };
}

function ProjectShellSkeleton() {
  return (
    <div className="min-h-screen bg-background">
      <div className="h-12 bg-topbar" />
      <div className="flex">
        <div className="hidden w-60 shrink-0 border-e border-border bg-card p-3 md:block">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} className="mb-2 h-8 rounded-sm" />
          ))}
        </div>
        <div className="mx-auto w-full max-w-[1200px] px-6 py-5">
          <Skeleton className="mb-6 h-8 w-48 rounded" />
          <div className="grid grid-cols-3 gap-6">
            <Skeleton className="h-28 rounded" />
            <Skeleton className="h-28 rounded" />
            <Skeleton className="h-28 rounded" />
          </div>
          <Skeleton className="mt-6 h-64 rounded" />
        </div>
      </div>
    </div>
  );
}

function ProjectGate({ slug, navigate, children }) {
  const [project, setProject] = React.useState(null);
  const [error, setError] = React.useState(false);
  React.useEffect(() => {
    let alive = true;
    setProject(null);
    setError(false);
    api
      .getProject(slug)
      .then((p) => alive && setProject(p))
      .catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
  }, [slug]);
  if (error) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4">
        <p className="text-body-md text-muted-foreground">Проект не найден</p>
        <button
          type="button"
          onClick={() => navigate("/")}
          className="text-body-sm text-primary hover:underline"
        >
          К списку проектов
        </button>
      </div>
    );
  }
  if (!project) return <ProjectShellSkeleton />;
  return children(project);
}

function App() {
  const { pathname, search, navigate } = useLocation();
  const query = React.useMemo(
    () => Object.fromEntries(new URLSearchParams(search)),
    [search],
  );
  const parts = pathname.split("/").filter(Boolean);

  let screen = null;

  if (parts.length === 0) {
    screen = <ProjectsListScreen navigate={navigate} />;
  } else if (parts[0] === "dev") {
    screen = <StatesCatalogueScreen navigate={navigate} />;
  } else if (parts[0] === "emails") {
    screen = (
      <EmailPreviewScreen kind={parts[1] || "completion"} navigate={navigate} />
    );
  } else if (parts[0] === "advanced") {
    screen = <AdvancedPlaceholderScreen url={query.url} navigate={navigate} />;
  } else if (parts[0] === "projects" && parts[1]) {
    const slug = parts[1];
    const section = parts[2];
    screen = (
      <ProjectGate slug={slug} navigate={navigate}>
        {(project) => {
          if (!section) {
            return project.localized ? (
              <ProjectOverviewScreen project={project} navigate={navigate} />
            ) : (
              <EmptyProjectScreen project={project} navigate={navigate} />
            );
          }
          if (section === "localize")
            return (
              <UniversalWizardScreen project={project} navigate={navigate} />
            );
          if (section === "localize-legacy")
            return <WizardScreen project={project} navigate={navigate} />;
          if (section === "upload")
            return <UploadScreen project={project} navigate={navigate} />;
          if (section === "decisions")
            return (
              <DecisionsQueueScreen
                project={project}
                initialQuery={query}
                navigate={navigate}
              />
            );
          if (section === "glossary")
            return <GlossaryScreen project={project} navigate={navigate} />;
          if (section === "download")
            return <DownloadScreen project={project} navigate={navigate} />;
          if (section === "settings")
            return <SettingsScreen project={project} navigate={navigate} />;
          if (section === "runs")
            return (
              <RunCardScreen
                project={project}
                runId={parts[3]}
                navigate={navigate}
              />
            );
          return (
            <ProjectOverviewScreen project={project} navigate={navigate} />
          );
        }}
      </ProjectGate>
    );
  } else {
    screen = <ProjectsListScreen navigate={navigate} />;
  }

  return (
    <>
      {screen}
      <Toaster position="bottom-left" />
    </>
  );
}

export default App;
