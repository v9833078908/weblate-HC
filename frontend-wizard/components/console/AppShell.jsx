"use client";

import {
  BookText,
  ChevronDown,
  ChevronRight,
  Download,
  ExternalLink,
  LayoutDashboard,
  ListChecks,
  Settings,
  Upload,
} from "lucide-react";
import * as React from "react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import * as api from "@/src/api/client";

const ADVANCED_TIP = "Появится после первой локализации";

function SidebarItem({ item, active, navigate }) {
  const Icon = item.icon;
  const disabled = item.disabled;
  const content = (
    <button
      type="button"
      disabled={disabled}
      onClick={() => !disabled && navigate(item.path)}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex w-full items-center gap-3 rounded-sm px-3 py-2 text-body-sm text-start transition-colors",
        active
          ? "bg-accent font-semibold text-accent-foreground"
          : "text-foreground hover:bg-muted",
        disabled &&
          "cursor-not-allowed text-muted-foreground hover:bg-transparent",
      )}
    >
      <Icon className="h-4 w-4 shrink-0" aria-hidden />
      <span className="flex-1 truncate">{item.label}</span>
      {typeof item.badge === "number" && item.badge > 0 && (
        <span className="ms-auto inline-flex min-w-[20px] items-center justify-center rounded-sm bg-warning-surface px-1.5 py-0.5 text-label-caps text-warning">
          {item.badge}
        </span>
      )}
    </button>
  );
  if (disabled) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="block">{content}</span>
        </TooltipTrigger>
        <TooltipContent side="right">{ADVANCED_TIP}</TooltipContent>
      </Tooltip>
    );
  }
  return content;
}

export function AppShell({
  project,
  active,
  breadcrumb = [],
  navigate,
  children,
}) {
  const [me, setMe] = React.useState(null);
  const [projects, setProjects] = React.useState([]);

  React.useEffect(() => {
    api
      .getMe()
      .then(setMe)
      .catch(() => {});
    api
      .getProjects()
      .then(setProjects)
      .catch(() => {});
  }, []);

  const slug = project?.slug;
  const localized = !!project?.localized;
  const nav = slug
    ? [
        {
          key: "overview",
          label: "Обзор",
          icon: LayoutDashboard,
          path: `/projects/${slug}`,
          disabled: false,
        },
        {
          key: "upload",
          label: "Загрузить",
          icon: Upload,
          path: `/projects/${slug}/upload`,
          disabled: !localized,
        },
        {
          key: "decisions",
          label: "Требуют решения",
          icon: ListChecks,
          path: `/projects/${slug}/decisions`,
          disabled: !localized,
          badge: project?.decisions_count,
        },
        {
          key: "glossary",
          label: "Глоссарий",
          icon: BookText,
          path: `/projects/${slug}/glossary`,
          disabled: !localized,
        },
        {
          key: "download",
          label: "Скачать",
          icon: Download,
          path: `/projects/${slug}/download`,
          disabled: !localized,
        },
        {
          key: "settings",
          label: "Настройки проекта",
          icon: Settings,
          path: `/projects/${slug}/settings`,
          disabled: false,
        },
      ]
    : [];

  const weblateProjectUrl = slug
    ? `https://weblate.hcgameloc.internal/projects/${slug}/`
    : "https://weblate.hcgameloc.internal/";

  return (
    <TooltipProvider delayDuration={200}>
      <div className="min-h-screen bg-background">
        {/* skip link */}
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-sm focus:bg-card focus:px-3 focus:py-2 focus:text-body-sm"
        >
          Перейти к содержимому
        </a>

        {/* top bar 48px */}
        <header className="sticky top-0 z-40 flex h-12 items-center gap-4 bg-topbar px-4 text-topbar-foreground">
          <button
            type="button"
            onClick={() => navigate("/")}
            className="text-label-strong text-topbar-foreground transition-colors hover:text-topbar-foreground-hover"
          >
            HCGameLoc
          </button>

          {project && (
            <DropdownMenu>
              <DropdownMenuTrigger className="inline-flex items-center gap-1.5 rounded-sm px-2 py-1 text-body-sm text-topbar-foreground transition-colors hover:text-topbar-foreground-hover">
                {project.name}
                <ChevronDown className="h-4 w-4" aria-hidden />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-64">
                <DropdownMenuLabel>Проекты</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {projects.map((p) => (
                  <DropdownMenuItem
                    key={p.slug}
                    onClick={() => navigate(`/projects/${p.slug}`)}
                  >
                    {p.name}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}

          <div className="ms-auto flex items-center gap-4">
            <a
              href={weblateProjectUrl}
              onClick={(e) => {
                e.preventDefault();
                navigate(
                  `/advanced?url=${encodeURIComponent(weblateProjectUrl)}`,
                );
              }}
              className="inline-flex items-center gap-1 text-body-sm text-topbar-foreground transition-colors hover:text-topbar-foreground-hover"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
              Открыть в Weblate
            </a>
            <DropdownMenu>
              <DropdownMenuTrigger className="text-body-sm text-topbar-foreground transition-colors hover:text-topbar-foreground-hover">
                {me?.full_name || "…"}
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>{me?.full_name}</DropdownMenuLabel>
                <DropdownMenuItem disabled>{me?.username}</DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => navigate("/dev/states")}>
                  Каталог состояний (/dev)
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        <div className="flex">
          {slug && (
            <aside className="sticky top-12 hidden h-[calc(100vh-48px)] w-60 shrink-0 border-e border-border bg-card p-3 md:block">
              <nav className="flex flex-col gap-0.5">
                {nav.map((item) => (
                  <SidebarItem
                    key={item.key}
                    item={item}
                    active={active === item.key}
                    navigate={navigate}
                  />
                ))}
                <div className="my-2 h-px bg-border" />
                <button
                  type="button"
                  onClick={() =>
                    navigate(
                      `/advanced?url=${encodeURIComponent(weblateProjectUrl)}`,
                    )
                  }
                  className="flex w-full items-center gap-3 rounded-sm px-3 py-2 text-body-sm text-muted-foreground transition-colors hover:bg-muted"
                >
                  <ExternalLink className="h-4 w-4 shrink-0" aria-hidden />
                  <span className="flex-1 text-start">Advanced → Weblate</span>
                </button>
              </nav>
            </aside>
          )}

          <main id="content" className="min-w-0 flex-1">
            <div className="mx-auto w-full max-w-[1200px] px-6 py-5">
              {breadcrumb.length > 0 && (
                <nav
                  aria-label="breadcrumb"
                  className="mb-4 flex flex-wrap items-center gap-1 text-body-sm text-muted-foreground"
                >
                  {breadcrumb.map((b, i) => (
                    // biome-ignore lint/suspicious/noArrayIndexKey: static breadcrumb chain, never reordered; pathless items have no stable id
                    <React.Fragment key={i}>
                      {i > 0 && (
                        <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                      )}
                      {b.path ? (
                        <button
                          type="button"
                          onClick={() => navigate(b.path)}
                          className="hover:text-foreground hover:underline"
                        >
                          {b.label}
                        </button>
                      ) : (
                        <span className="text-foreground">{b.label}</span>
                      )}
                    </React.Fragment>
                  ))}
                </nav>
              )}
              {children}
            </div>
          </main>
        </div>
      </div>
    </TooltipProvider>
  );
}
