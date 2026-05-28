import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";


type Props = {
  nodeId: string;
  onSuccess?: () => void;
};


export function RetryProvisioningButton({ nodeId, onSuccess }: Props) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: async () => {
      const resp = await fetch(`/api/v1/nodes/${nodeId}/provisioning/retry`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!resp.ok) throw new Error(`retry failed: ${resp.status}`);
      return resp.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["node-provisioning", nodeId] });
      onSuccess?.();
    },
  });

  return (
    <button
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
      className="inline-flex items-center gap-2 px-3 py-2 rounded-md
                 bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
    >
      {mutation.isPending
        ? <Loader2 className="h-4 w-4 animate-spin" />
        : <RefreshCw className="h-4 w-4" />}
      Retry
    </button>
  );
}
