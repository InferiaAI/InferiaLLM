import { Copy } from "lucide-react";
import { useState } from "react";
import type { DepinDetails } from "@/services/nodeService";


// The CopyableField/PlainField helpers in AWSMetadataGrid are file-local
// (not exported), so we duplicate the two tiny helpers here rather than
// coupling the two grids.
function CopyableField({ label, value }: { label: string; value: string | null }) {
  const [copied, setCopied] = useState(false);
  const v = value ?? "—";
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="font-mono text-sm flex items-center gap-2">
        {v}
        {value && (
          <button
            aria-label={`Copy ${label}`}
            onClick={() => {
              navigator.clipboard.writeText(value);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
            className="text-muted-foreground hover:text-foreground"
          >
            <Copy className="h-3 w-3" />
          </button>
        )}
        {copied && <span className="text-xs text-green-600">copied</span>}
      </span>
    </div>
  );
}


function PlainField({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="font-mono text-sm">{value ?? "—"}</span>
    </div>
  );
}


export function NosanaMetadataGrid({ details }: { details: DepinDetails }) {
  const gpu =
    details.gpu_total === null || details.gpu_total === undefined
      ? null
      : String(details.gpu_total);
  return (
    <div className="rounded-lg border p-4 grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2">
      <CopyableField label="Job address"        value={details.job_address} />
      <CopyableField label="Node address"       value={details.node_address} />
      <CopyableField label="Deployment address" value={details.deployment_address} />
      <CopyableField label="Run address"        value={details.run_address} />
      <CopyableField label="Service URL"        value={details.service_url} />
      <PlainField    label="Market"             value={details.market} />
      <PlainField    label="GPU"                value={gpu} />
      <PlainField    label="Price"              value={details.price} />
      <PlainField    label="Job state"          value={details.job_state} />
      <PlainField    label="Image"              value={details.image} />
      <PlainField    label="Mode"               value={details.mode} />
      <PlainField    label="Tx"                 value={details.tx} />
      <PlainField    label="Credential name"    value={details.provider_credential_name} />
      <PlainField    label="Created at"         value={details.created_at} />
    </div>
  );
}
