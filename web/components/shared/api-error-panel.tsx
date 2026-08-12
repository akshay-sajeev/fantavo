import { AlertTriangle } from "lucide-react";
import { ApiError } from "@/lib/api";

export function ApiErrorPanel({ error }: { error: unknown }) {
  const message =
    error instanceof ApiError
      ? error.message
      : error instanceof Error
        ? error.message
        : "Unknown error";
  const status = error instanceof ApiError ? error.status : undefined;

  return (
    <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-foreground">
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" aria-hidden="true" />
      <div>
        <p className="font-medium text-destructive">
          {status ? `Could not load data (HTTP ${status})` : "Could not load data"}
        </p>
        <p className="mt-1 text-muted-foreground">{message}</p>
      </div>
    </div>
  );
}
