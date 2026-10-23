"use client";

import { ArrowRight, Download, FileUp, Languages } from "lucide-react";
import { AppShell } from "@/components/console/AppShell";
import { WeblateLink } from "@/components/console/primitives";
import { Button } from "@/components/ui/button";

export function EmptyProjectScreen({ project, navigate }) {
  return (
    <AppShell
      project={project}
      active="overview"
      navigate={navigate}
      breadcrumb={[{ label: "Проекты", path: "/" }, { label: project.name }]}
    >
      <div className="mx-auto max-w-3xl py-10">
        <h1 className="text-display-lg text-foreground">
          Это автоматический wizard. Загрузите лок-кит
        </h1>

        <div className="mt-8 space-y-3 text-body-md text-muted-foreground">
          <div className="flex items-center gap-3">
            <FileUp className="h-5 w-5 shrink-0 text-primary" aria-hidden />
            <span>1. Загрузите лок-кит — таблицу с ключами и текстами.</span>
          </div>
          <div className="flex items-center gap-3">
            <Languages className="h-5 w-5 shrink-0 text-primary" aria-hidden />
            <span>
              2. Платформа переведёт его на все языки проекта и проверит
              качество.
            </span>
          </div>
          <div className="flex items-center gap-3">
            <Download className="h-5 w-5 shrink-0 text-primary" aria-hidden />
            <span>3. Скачайте готовый результат в нужном формате.</span>
          </div>
        </div>

        <div className="mt-10 flex items-center gap-4">
          <Button
            className="h-9 rounded-sm"
            onClick={() => navigate(`/projects/${project.slug}/localize`)}
          >
            Загрузить лок-кит <ArrowRight className="h-4 w-4" />
          </Button>
          <WeblateLink
            url={`https://weblate.hcgameloc.internal/projects/${project.slug}/`}
            navigate={navigate}
          />
        </div>
      </div>
    </AppShell>
  );
}
