# 0006 — Multimodal through workload classes and per-class pools

## Context
The brief requires a unified API across text, embeddings, STT/TTS, image and video, with workload-specific scheduling: streaming, real-time audio, video pipelines, batch, embeddings, interactive chat, long context.

Handlers for completion, embedding, audio, image and video already exist (`src/inference/core/handlers/`), and InferiaDiffusion serves image/video/TTS as the `inferia-diffusion` engine.

## Decision
- **One API family:** OpenAI-compatible where a standard exists, plus an async job API for video and batch.
- **Every request gets a `WorkloadClass`** (`interfaces.md` §3): `realtime_audio`, `interactive_chat`, `long_context`, `embedding`, `video_job`, `batch`.
- **The class selects** a pool, queue policy, SLO, admission rule and preemption rule.
- **Engines:** vLLM (chat, vision, Whisper STT, embeddings), vLLM-Omni (TTS/audio), InferiaDiffusion (image/video).
- **Toward llm-d:** classes map to `InferenceObjective` priorities, and tenants map to the fairness id (`interfaces.md` §4).

## Alternatives considered
- **One shared pool with one FIFO queue.** Rejected: real-time audio and interactive chat would suffer from batch and video work.
- **A separate API per modality.** Rejected: inconsistent client experience and duplicated governance.

## Consequences
- Class assignment is policy-controlled, so tenants can't self-escalate.
- `WorkloadClass` is per request and distinct from the existing deployment-level `workload_type` (`inference`/`external`).
- InferiaDiffusion needs durable job storage before it can scale (ADR 0007).

## Status
Accepted (2026-10-31).

## Sources
- `brief.md` Part A §8–9
- `plan.md` WS6
