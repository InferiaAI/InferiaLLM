import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { ALL_PHASES, type ProvisioningSummary, type ProvisioningPhase } from "@/services/provisioningService";

const PHASE_LABELS: Record<string, string> = {
  prepare: "Prepare credentials & user-data",
  ami_lookup: "Look up AMI",
  pulumi_init: "Initialize Pulumi stack",
  pulumi_up: "Provision EC2 instance",
  ec2_running: "EC2 instance running",
  cloud_init: "Boot & install worker",
  worker_bootstrap: "Worker bootstrap",
  ready: "Ready",
};

function PhaseIcon({ status }: { status: ProvisioningPhase["status"] | "pending" }) {
  if (status === "running") {
    return <Loader2 className="w-4 h-4 animate-spin text-ember-500" data-icon="spinner" />;
  }
  if (status === "succeeded") {
    return <CheckCircle2 className="w-4 h-4 text-emerald-500" data-icon="check" />;
  }
  if (status === "failed") {
    return <XCircle className="w-4 h-4 text-red-500" data-icon="error" />;
  }
  return <Circle className="w-4 h-4 text-muted-foreground/40" data-icon="pending" />;
}

export default function ProvisioningStatus(
  { summary, attemptCount = 0 }:
  { summary: ProvisioningSummary; attemptCount?: number },
) {
  const byPhase = new Map(summary.phases.map(p => [p.phase, p]));
  const failed = summary.phases.find(p => p.status === "failed");
  // Only surface the "Attempt N" badge when we've genuinely retried —
  // a first-attempt job has attempt_count=1 and no badge is helpful.
  const showAttemptBadge = attemptCount > 1;

  return (
    <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-mono text-sm font-semibold">Provisioning Status</h3>
        {showAttemptBadge && (
          <span
            className="px-2 py-0.5 rounded border border-amber-500/30
                       bg-amber-500/10 text-amber-700 dark:text-amber-300
                       text-xs font-medium"
            data-testid="provisioning-attempt-badge"
          >
            Attempt {attemptCount}
          </span>
        )}
      </div>
      {failed && (
        <div className="mb-4 rounded-md border border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300 px-3 py-2 text-sm">
          <div className="font-semibold">Provisioning failed at {PHASE_LABELS[failed.phase] || failed.phase}</div>
          {failed.last_message && (
            <div className="font-mono text-xs mt-1 break-all">{failed.last_message}</div>
          )}
        </div>
      )}
      <ol className="space-y-2">
        {ALL_PHASES.map((phase) => {
          const p = byPhase.get(phase);
          const status: ProvisioningPhase["status"] | "pending" = p?.status ?? "pending";
          return (
            <li
              key={phase}
              data-testid={`phase-row-${phase}`}
              className={cn(
                "flex items-start gap-3 text-sm",
                status === "pending" && "text-muted-foreground/60",
              )}
            >
              <div className="mt-0.5"><PhaseIcon status={status} /></div>
              <div className="flex-1">
                <div className="font-medium">{PHASE_LABELS[phase] || phase}</div>
                {status === "running" && p?.last_message && (
                  <div className="text-xs text-muted-foreground font-mono mt-0.5 break-all">
                    {p.last_message}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
