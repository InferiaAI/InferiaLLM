import { useEffect, useRef, useState } from "react";
import { ConfigService } from "@/services/configService";
import { toast } from "sonner";

type Ami = { ami_id: string; vllm_tag?: string; region: string; created: string };

const PHASE_LABELS: Record<string, string> = {
    "starting": "Starting…",
    "launching-builder": "Launching builder…",
    "waiting-for-ssm": "Waiting for builder…",
    "installing-and-pulling": "Installing + pulling vLLM image…",
    "stopping-builder": "Stopping builder…",
    "creating-ami": "Creating AMI…",
    "waiting-for-ami": "Waiting for AMI to become available…",
    "done": "Done",
    "failed": "Failed",
};

export function EngineAmiSection() {
    const [region, setRegion] = useState("us-east-1");
    const [amis, setAmis] = useState<Ami[]>([]);
    const [vllmTag, setVllmTag] = useState("");
    const [baking, setBaking] = useState(false);
    const [status, setStatus] = useState("");
    const [bakePhase, setBakePhase] = useState("");
    const [bakeLog, setBakeLog] = useState<string[]>([]);
    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const logContainerRef = useRef<HTMLDivElement | null>(null);

    const refresh = async () => {
        try { setAmis(await ConfigService.listEngineAmis(region)); }
        catch { /* surface quietly; the list endpoint requires AWS creds + perms */ setAmis([]); }
    };
    useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [region]);
    // Clear any in-flight poll on unmount to avoid a state-update-after-unmount leak.
    useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

    // Auto-scroll the log pane to the bottom whenever new lines arrive.
    useEffect(() => {
        if (logContainerRef.current) {
            logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
        }
    }, [bakeLog]);

    const bake = async () => {
        setBaking(true); setStatus("running"); setBakePhase(""); setBakeLog([]);
        try {
            const { bake_id } = await ConfigService.startEngineBake({ region, vllm_tag: vllmTag || undefined });
            pollRef.current = setInterval(async () => {
                try {
                    const s = await ConfigService.pollBakeStatus(bake_id);
                    setStatus(`${s.status}${s.message ? ": " + s.message : ""}`);
                    setBakePhase(s.phase ?? "");
                    setBakeLog(s.log ?? []);
                    if (s.status === "succeeded" || s.status === "failed") {
                        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
                        setBaking(false);
                        if (s.status === "succeeded") { toast.success("Engine AMI baked"); refresh(); }
                        else toast.error("Bake failed: " + (s.message || "unknown"));
                    }
                } catch {
                    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
                    setBaking(false); setStatus("failed"); toast.error("Lost track of the bake");
                }
            }, 5000);
        } catch { setBaking(false); setStatus(""); toast.error("Failed to start bake"); }
    };

    const isBakeActive = baking;

    return (
        <div className="space-y-4 border border-border rounded-lg p-4">
            <h3 className="text-sm font-semibold">Engine Cache AMIs</h3>
            <p className="text-xs text-muted-foreground">
                Baked AMIs preload the vLLM engine image so cold GPU nodes skip the long image pull/extract.
                Baking requires the one-time SSM instance profile (<code className="font-mono">INFERIA_BAKE_SSM_INSTANCE_PROFILE</code>) + IAM permissions on the AWS credentials.
            </p>
            <div className="flex flex-wrap gap-2 items-end">
                <div><label className="text-xs block mb-1">Region</label><input value={region} onChange={e => setRegion(e.target.value)} className="h-9 w-36 rounded-md border border-input bg-background px-2 text-sm" /></div>
                <div><label className="text-xs block mb-1">vLLM tag (optional)</label><input value={vllmTag} onChange={e => setVllmTag(e.target.value)} placeholder="v0.22.1" className="h-9 w-32 rounded-md border border-input bg-background px-2 text-sm" /></div>
                <button type="button" disabled={baking} onClick={bake} className="h-9 px-3 text-xs font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50">{baking ? "Baking…" : "Bake new AMI"}</button>
            </div>
            {status && <p className="text-xs text-muted-foreground">Status: {status}</p>}
            {isBakeActive && (
                <div className="space-y-2">
                    {bakePhase && (
                        <div className="flex items-center gap-2">
                            <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-primary/10 text-primary border border-primary/20">
                                {PHASE_LABELS[bakePhase] ?? bakePhase}
                            </span>
                        </div>
                    )}
                    {bakeLog.length > 0 && (
                        <div
                            ref={logContainerRef}
                            className="max-h-64 overflow-y-auto rounded-md border border-border bg-muted/50 p-2"
                        >
                            <pre className="font-mono text-xs text-muted-foreground whitespace-pre-wrap break-all">
                                {bakeLog.slice(-200).join("\n")}
                            </pre>
                        </div>
                    )}
                </div>
            )}
            <table className="w-full text-xs">
                <thead><tr className="text-left text-muted-foreground border-b border-border"><th className="py-1">AMI</th><th>vLLM tag</th><th>Created</th></tr></thead>
                <tbody>
                    {amis.map(a => (<tr key={a.ami_id} className="border-b border-border/50"><td className="py-1 font-mono">{a.ami_id}</td><td>{a.vllm_tag || "-"}</td><td>{a.created}</td></tr>))}
                    {amis.length === 0 && <tr><td colSpan={3} className="py-2 text-muted-foreground">No baked AMIs yet.</td></tr>}
                </tbody>
            </table>
        </div>
    );
}
