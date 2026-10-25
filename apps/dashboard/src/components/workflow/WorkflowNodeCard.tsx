import { useEffect, useMemo, useRef, useState } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { Link } from "react-router-dom";
import { CheckCircle2, CloudUpload, Loader2, Mic, Square, Upload, X, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { NODE_KINDS, type ConfigField } from "@/lib/workflow/nodeKinds";
import type { Readiness } from "@/lib/workflow/readiness";
import { stats } from "@/lib/workflow/stats";
import type { NodeKind, NodeRun, Payload } from "@/lib/workflow/types";
import { useCanvas } from "./canvasContext";
import { useNow } from "./useNow";

export type FlowNode = Node<{ kind: NodeKind; config: Record<string, string> }, "step">;

const inputClass =
  "nodrag w-full px-2 py-1.5 rounded-md border bg-background text-xs focus:ring-1 focus:ring-ember-500 outline-none";

export function WorkflowNodeCard({ id, data, selected }: NodeProps<FlowNode>) {
  const spec = NODE_KINDS[data.kind];
  const { runs, readiness } = useCanvas();
  const run = runs[id];
  const ready = readiness[id];

  return (
    <div
      className={cn(
        "w-60 rounded-xl border bg-card text-card-foreground shadow-sm",
        selected && "ring-2 ring-ember-500",
        run?.status === "running" && "border-ember-500",
        run?.status === "error" && "border-red-500",
        !run && ready && ready.state !== "ready" && ready.state !== "selectModel" && "border-amber-500/70",
      )}
    >
      {spec.input && <Handle type="target" position={Position.Left} className="!h-3 !w-3 !bg-ember-500" />}
      <NodeHeader id={id} label={spec.label} run={run} ports={`${spec.input ?? "-"} -> ${spec.output ?? "-"}`} />
      <LatencyStats nodeId={id} />
      {ready && <ReadinessBanner nodeId={id} readiness={ready} />}
      <div className="space-y-2 p-3">
        {data.kind === "audioInput" && <AudioSource nodeId={id} />}
        {data.kind === "imageInput" && <FileSource nodeId={id} accept="image/*" />}
        {data.kind === "videoInput" && <FileSource nodeId={id} accept="video/*" />}
        {spec.fields.map((field) => (
          <Field key={field.key} nodeId={id} field={field} value={data.config[field.key] ?? ""} />
        ))}
        {run?.error && <p className="text-xs text-red-500">{run.error}</p>}
        {run?.output && data.kind !== "audioInput" && data.kind !== "imageInput" && (
          <Output payload={run.output} autoPlay={data.kind === "audioOutput"} />
        )}
      </div>
      {spec.output && <Handle type="source" position={Position.Right} className="!h-3 !w-3 !bg-ember-500" />}
    </div>
  );
}

function NodeHeader({ id, label, run, ports }: { id: string; label: string; run?: NodeRun; ports: string }) {
  const { removeNode } = useCanvas();
  return (
    <div className="flex items-center justify-between gap-2 border-b bg-muted/30 px-3 py-2">
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        <div className="font-mono text-[10px] text-muted-foreground">{ports}</div>
      </div>
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        {run?.status === "running" && <Loader2 className="h-3.5 w-3.5 animate-spin text-ember-500" />}
        {run?.status === "done" && <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />}
        {run?.status === "error" && <XCircle className="h-3.5 w-3.5 text-red-500" />}
        {run && run.status !== "idle" && <Elapsed run={run} />}
        <button
          type="button"
          aria-label={`Remove ${label}`}
          onClick={() => removeNode(id)}
          className="nodrag rounded p-0.5 hover:bg-muted hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

function Elapsed({ run }: { run: NodeRun }) {
  const live = run.status === "running";
  const now = useNow(live);
  const ms = live && run.startedAt !== undefined ? Math.max(0, now - run.startedAt) : run.ms;
  if (ms === undefined) return null;
  return (
    <span className={cn("font-mono tabular-nums", live && "text-ember-500")}>{Math.round(ms)} ms</span>
  );
}

function ReadinessBanner({ nodeId, readiness }: { nodeId: string; readiness: Readiness }) {
  const { deploy } = useCanvas();
  if (readiness.state === "ready" || readiness.state === "selectModel" || readiness.state === "deployed") return null;
  if (readiness.state === "needsExternal") {
    return (
      <div className="space-y-1.5 border-b bg-amber-500/5 px-3 py-2 text-[11px]">
        <span className="font-medium text-amber-600 dark:text-amber-400">Needs an external deployment</span>
        <p className="text-muted-foreground">
          Compute pools can't run this model type yet.{" "}
          <Link to="/dashboard/deployments/new" className="nodrag text-ember-600 underline">
            Add an external endpoint
          </Link>
        </p>
      </div>
    );
  }
  const { recommended } = readiness;
  const retry = readiness.state === "waiting" || readiness.state === "failed";
  return (
    <div className="space-y-1.5 border-b bg-amber-500/5 px-3 py-2 text-[11px]">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-amber-600 dark:text-amber-400">
          {readiness.state === "needsDeployment" && "Needs deployment"}
          {readiness.state === "deploying" && "Deploying..."}
          {readiness.state === "waiting" && "Waiting for compute"}
          {readiness.state === "failed" && "Deploy failed"}
        </span>
        {readiness.state === "deploying" ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-500" />
        ) : (
          <button
            type="button"
            onClick={() => deploy(nodeId)}
            className="nodrag flex items-center gap-1 rounded-md bg-ember-600 px-2 py-0.5 font-medium text-white hover:bg-ember-700"
          >
            <CloudUpload className="h-3 w-3" />
            {retry ? "Retry" : "Deploy"}
          </button>
        )}
      </div>
      <div className="text-muted-foreground">
        Recommended <span className="font-mono text-foreground">{recommended.modelId}</span> on {recommended.engine}
      </div>
      {retry && <p className="break-words text-red-500">{readiness.reason}</p>}
    </div>
  );
}

function LatencyStats({ nodeId }: { nodeId: string }) {
  const { history } = useCanvas();
  const s = stats(history[nodeId] ?? []);
  if (!s) return null;
  return (
    <div className="flex justify-between gap-2 border-b px-3 py-1 font-mono text-[10px] tabular-nums text-muted-foreground">
      <span>last {s.last}</span>
      <span>p50 {s.p50}</span>
      <span>p95 {s.p95} ms</span>
      <span>n={s.count}</span>
    </div>
  );
}

function Field({ nodeId, field, value }: { nodeId: string; field: ConfigField; value: string }) {
  const { deployments, setConfig } = useCanvas();
  const onChange = (v: string) => setConfig(nodeId, field.key, v);

  let control;
  if (field.type === "model") {
    const options = deployments.filter((d) => d.model_type === field.modelType).map((d) => d.modelName);
    if (value && !options.includes(value)) options.unshift(value);
    control = (
      <select value={value} onChange={(e) => onChange(e.target.value)} className={inputClass}>
        <option value="">{options.length ? "Select a model" : `No ${field.modelType} deployments`}</option>
        {options.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    );
  } else if (field.type === "textarea") {
    control = (
      <textarea
        rows={3}
        value={value}
        placeholder={field.placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={cn(inputClass, "nowheel resize-y")}
      />
    );
  } else {
    control = (
      <input value={value} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} className={inputClass} />
    );
  }

  return (
    <label className="block space-y-1">
      <span className="text-[11px] font-medium text-muted-foreground">{field.label}</span>
      {control}
    </label>
  );
}

function AudioSource({ nodeId }: { nodeId: string }) {
  const { inputs, setInput } = useCanvas();
  const [recording, setRecording] = useState(false);
  const [micError, setMicError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  // The recorder outlives the render that started it; stopping must reach the current run state.
  const setInputRef = useRef(setInput);
  useEffect(() => {
    setInputRef.current = setInput;
  });
  const blob = inputs[nodeId];

  useEffect(() => () => recorderRef.current?.stream.getTracks().forEach((t) => t.stop()), []);

  const start = async () => {
    setMicError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      recorder.ondataavailable = (e) => chunks.push(e.data);
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        setInputRef.current(nodeId, new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
      };
      recorder.start();
      recorderRef.current = recorder;
      setRecording(true);
    } catch (err) {
      setMicError(err instanceof Error ? err.message : "Microphone unavailable");
    }
  };

  const stop = () => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  };

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={recording ? stop : start}
          className={cn(
            "nodrag flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium text-white",
            recording ? "bg-red-500 hover:bg-red-600" : "bg-ember-600 hover:bg-ember-700",
          )}
        >
          {recording ? <Square className="h-3.5 w-3.5" /> : <Mic className="h-3.5 w-3.5" />}
          {recording ? "Stop and run" : "Record"}
        </button>
        <UploadButton accept="audio/*" onFile={(file) => setInput(nodeId, file)} />
      </div>
      {recording && <p className="animate-pulse text-xs text-red-500">Recording...</p>}
      {micError && <p className="text-xs text-red-500">{micError}</p>}
      {blob && !recording && <AudioPlayer blob={blob} />}
    </div>
  );
}

function UploadButton({ accept, onFile, wide = false }: { accept: string; onFile: (file: File) => void; wide?: boolean }) {
  return (
    <label
      className={cn(
        "nodrag flex cursor-pointer items-center justify-center gap-1.5 rounded-md border px-2 py-1.5 text-xs hover:bg-muted",
        wide && "w-full",
      )}
    >
      <Upload className="h-3.5 w-3.5" />
      Upload
      <input
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFile(file);
          e.target.value = "";
        }}
      />
    </label>
  );
}

function FileSource({ nodeId, accept }: { nodeId: string; accept: "image/*" | "video/*" }) {
  const { inputs, setInput } = useCanvas();
  const blob = inputs[nodeId];
  return (
    <div className="space-y-2">
      <UploadButton accept={accept} onFile={(file) => setInput(nodeId, file)} wide />
      {blob && (accept === "image/*" ? <Thumbnails blobs={[blob]} /> : <VideoPreview blob={blob} />)}
    </div>
  );
}

function useObjectUrls(blobs: Blob[]): string[] {
  const urls = useMemo(() => blobs.map((b) => URL.createObjectURL(b)), [blobs]);
  useEffect(() => () => urls.forEach((u) => URL.revokeObjectURL(u)), [urls]);
  return urls;
}

function Thumbnails({ blobs }: { blobs: Blob[] }) {
  const urls = useObjectUrls(blobs);
  return (
    <div className={cn("grid gap-1", urls.length > 1 && "grid-cols-2")}>
      {urls.map((u, i) => (
        <img key={u} src={u} alt={`Image ${i + 1}`} className="w-full rounded-md border object-contain" />
      ))}
    </div>
  );
}

function VideoPreview({ blob }: { blob: Blob }) {
  const [url] = useObjectUrls(useMemo(() => [blob], [blob]));
  return <video src={url} controls muted className="nodrag w-full rounded-md border" />;
}

function Output({ payload, autoPlay }: { payload: Payload; autoPlay: boolean }) {
  if (payload.type === "audio") return <AudioPlayer blob={payload.blob} autoPlay={autoPlay} />;
  if (payload.type === "image") return <Thumbnails blobs={payload.blobs} />;
  return (
    <div className="nowheel nodrag max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md border bg-muted/40 p-2 text-xs">
      {payload.text || <span className="text-muted-foreground">(empty)</span>}
    </div>
  );
}

function AudioPlayer({ blob, autoPlay = false }: { blob: Blob; autoPlay?: boolean }) {
  const url = useMemo(() => URL.createObjectURL(blob), [blob]);
  useEffect(() => () => URL.revokeObjectURL(url), [url]);
  return <audio controls autoPlay={autoPlay} src={url} className="nodrag h-8 w-full" />;
}
