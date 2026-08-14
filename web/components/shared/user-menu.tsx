"use client";

import { useState } from "react";
import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";

export function UserMenu({ email }: { email: string }) {
  const [loggingOut, setLoggingOut] = useState(false);

  async function handleLogout() {
    setLoggingOut(true);
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
    window.location.href = "/login";
  }

  return (
    <div className="flex items-center gap-3">
      <span className="hidden text-sm text-muted-foreground sm:inline">{email}</span>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleLogout}
        disabled={loggingOut}
        className="cursor-pointer"
      >
        <LogOut className="h-4 w-4" aria-hidden="true" />
        Log out
      </Button>
    </div>
  );
}
