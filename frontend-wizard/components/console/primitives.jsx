"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  ExternalLink,
  Loader2,
  Radio,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ---- Status badge (icon + text, never colour-only) ----
const STATUS = {
  ready: {
    label: "Готово",
    icon: CheckCircle2,
    cls: "bg-success text-success-foreground border-transparent",
  },
  completed: {
    label: "Завершён",
    icon: CheckCircle2,
    cls: "bg-success text-success-foreground border-transparent",
  },
  translating: {
    label: "Переводится",
    icon: Loader2,
    cls: "bg-info-surface text-info-strong border-transparent",
    spin: true,
  },
  running: {
    label: "Выполняется",
    icon: Loader2,
    cls: "bg-info-surface text-info-strong border-transparent",
    spin: true,
  },
  queued: {
    label: "В очереди",
    icon: Clock,
    cls: "bg-secondary text-secondary-foreground border-border",
  },
  decisions: {
    label: "Требует решения",
    icon: AlertTriangle,
    cls: "bg-warning-surface text-warning border-transparent",
  },
  stalled: {
    label: "Нет обновлений",
    icon: Radio,
    cls: "bg-background text-foreground border-input",
    pulse: true,
  },
  error: {
    label: "Ошибка",
    icon: XCircle,
    cls: "bg-destructive text-destructive-foreground border-transparent",
  },
};

export function StatusBadge({ status, className }) {
  const s = STATUS[status] || STATUS.queued;
  const Icon = s.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 text-label-caps uppercase",
        s.cls,
        className,
      )}
      role="status"
    >
      <Icon
        className={cn(
          "h-3.5 w-3.5 shrink-0",
          s.spin && "animate-spin",
          s.pulse && "animate-pulse",
        )}
        aria-hidden
      />
      <span>{s.label}</span>
    </span>
  );
}

// ---- Decision kind badge ----
const KIND = {
  blocking: {
    label: "Блокирует",
    icon: XCircle,
    cls: "bg-background text-destructive border-destructive",
  },
  judge_critical: {
    label: "Судья: критично",
    icon: AlertTriangle,
    cls: "bg-warning-surface text-warning border-transparent",
  },
  judge_note: {
    label: "Судья: замечание",
    icon: AlertTriangle,
    cls: "bg-info-surface text-info-strong border-transparent",
  },
};

export function KindBadge({ kind, className }) {
  const k = KIND[kind] || KIND.judge_note;
  const Icon = k.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 text-label-caps uppercase",
        k.cls,
        className,
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span>{k.label}</span>
    </span>
  );
}

// ---- Flat card with optional header band ----
export function ConsoleCard({
  title,
  actions,
  children,
  className,
  bodyClassName,
}) {
  return (
    <section className={cn("rounded border border-border bg-card", className)}>
      {title && (
        <header className="flex items-center justify-between gap-3 rounded-t border-b border-border bg-accent px-4 py-2.5">
          <h2 className="text-heading-card text-card-foreground">{title}</h2>
          {actions}
        </header>
      )}
      <div className={cn("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}

// ---- Metric tile ----
export function MetricTile({ value, label, action, tone = "default" }) {
  return (
    <div className="rounded border border-border bg-card p-4">
      <div
        className={cn(
          "text-metric-lg tabular-nums",
          tone === "warning" && "text-warning",
          tone === "success" && "text-success",
        )}
      >
        {value}
      </div>
      <div className="mt-1 text-body-sm text-muted-foreground">{label}</div>
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

// ---- "Открыть в Weblate" quiet link ----
export function WeblateLink({
  url,
  label = "Открыть в Weblate",
  navigate,
  className,
}) {
  const onClick = (e) => {
    e.preventDefault();
    if (navigate) navigate(`/advanced?url=${encodeURIComponent(url || "")}`);
  };
  return (
    <a
      href={url || "#"}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 text-body-sm text-primary underline-offset-4 hover:underline",
        className,
      )}
    >
      <ExternalLink className="h-3.5 w-3.5" aria-hidden />
      {label}
    </a>
  );
}

export function Money({ value, className }) {
  const v = String(value).startsWith("$") ? value : `$${value}`;
  return <span className={cn("tabular-nums", className)}>{v}</span>;
}

// ---- thin n/N progress (no charts) ----
export function ThinProgress({ value, max, className }) {
  const pct = max ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div
      className={cn(
        "h-1 w-full overflow-hidden rounded-full bg-muted",
        className,
      )}
    >
      <div
        className="h-full bg-primary transition-[width] duration-300 ease-linear"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function EmptyState({ icon: Icon, title, description, action }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      {Icon && (
        <Icon className="mb-4 h-10 w-10 text-muted-foreground" aria-hidden />
      )}
      <h3 className="text-heading-card text-foreground">{title}</h3>
      {description && (
        <p className="mt-2 max-w-md text-body-sm text-muted-foreground">
          {description}
        </p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
