"use client";

import * as React from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import * as api from "@/src/api/client";

export function RowsSheet({ uploadId, req, onClose }) {
  const [rows, setRows] = React.useState(null);
  React.useEffect(() => {
    if (!req) {
      setRows(null);
      return;
    }
    setRows(null);
    api.getUploadRows(uploadId, req.set, req.value).then(setRows);
  }, [req, uploadId]);

  return (
    <Sheet open={!!req} onOpenChange={(v) => !v && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-[560px]">
        <SheetHeader>
          <SheetTitle>{req?.title || "Строки"}</SheetTitle>
        </SheetHeader>
        <div className="mt-4">
          {rows === null ? (
            <div className="space-y-2">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-12 rounded-sm" />
              ))}
            </div>
          ) : rows.length === 0 ? (
            <p className="text-body-sm text-muted-foreground">Нет строк.</p>
          ) : (
            <ul className="divide-y divide-border rounded-sm border border-border">
              {rows.map((r) => (
                <li key={r.n} className="p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-body-sm text-muted-foreground">
                      строка {r.n}
                    </span>
                    {r.reason && (
                      <span className="rounded-sm bg-warning-surface px-2 py-0.5 text-label-caps uppercase text-warning">
                        {r.reason}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-body-sm">
                    {Object.entries(r.cells).map(([k, v]) => (
                      <span key={k}>
                        <span className="font-mono text-muted-foreground">
                          {k}:
                        </span>{" "}
                        <span className="text-foreground">
                          {v === "" ? "—" : v}
                        </span>
                      </span>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
