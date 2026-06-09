import { isExternalAuthMode } from "@/lib/authMode";
import { type ReactNode } from "react";

export function ExternalIdentityGuard({ children }: { children: ReactNode }) {
  if (isExternalAuthMode()) {
    return (
      <div className="p-8 max-w-xl">
        <h2 className="text-lg font-semibold">Managed by your identity provider</h2>
        <p className="text-sm text-muted-foreground mt-2">
          Organizations, users, teams, and roles are managed centrally by your
          identity provider in this deployment. There's nothing to configure here.
        </p>
      </div>
    );
  }
  return <>{children}</>;
}
