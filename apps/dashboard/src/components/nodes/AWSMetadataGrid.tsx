import { Copy } from "lucide-react";
import { useState } from "react";


export type AWSMetadata = {
  instance_class: "normal_gpu" | "heavy_gpu" | "cpu" | null;
  instance_type: string | null;
  region: string | null;
  ami_id: string | null;
  instance_id: string | null;
  public_dns: string | null;
};


const CLASS_LABEL: Record<string, string> = {
  normal_gpu: "Normal GPU",
  heavy_gpu:  "Heavy GPU",
  cpu:        "CPU only",
};


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


export function AWSMetadataGrid({ metadata }: { metadata: AWSMetadata }) {
  return (
    <div className="rounded-lg border p-4 grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2">
      <PlainField label="Instance class" value={
        metadata.instance_class ? CLASS_LABEL[metadata.instance_class] : null
      } />
      <CopyableField label="Instance ID"  value={metadata.instance_id} />
      <PlainField    label="Instance type" value={metadata.instance_type} />
      <CopyableField label="Public DNS"   value={metadata.public_dns} />
      <PlainField    label="Region"       value={metadata.region} />
      <PlainField    label="AMI"          value={metadata.ami_id} />
    </div>
  );
}
