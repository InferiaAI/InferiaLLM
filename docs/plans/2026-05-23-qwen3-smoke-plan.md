# Qwen3-0.6B End-to-End Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing `provider → compute pool → node → deployment → sandbox` flow work end-to-end for both the manual local worker and the Pulumi-provisioned AWS worker, running Qwen3-0.6B under both vLLM and Ollama, verified by two smoke scripts and one Playwright spec.

**Architecture:** Approach A from the spec — minimal patches on existing scaffolding. Add an Ollama pull-after-ready step in `inferia-worker`, build a smoke helper library plus two scripts (`local`, `aws`) in InferiaLLM, add a Playwright spec, and a `tag_suffix` input on the worker's GHCR workflow. No new product surface.

**Tech Stack:** Go 1.26 + testify (worker), Python 3.10-3.12 + httpx + respx + pytest (smoke lib), TypeScript + Playwright (UI), Docker Compose v2, Pulumi (existing), AWS EC2 g4dn.xlarge.

**Repos and branches:**
- `inferia-worker` on `feat/aws-ec2-bootstrap` (path: `/storage/intern/hooman/work/inferia-worker`)
- `InferiaLLM` on `feat/aws-ec2-provisioning` (path: `/storage/intern/hooman/work/InferiaLLM`)

**Commit policy:** Sign every commit with `~/.ssh/id_ed25519_gh` per the user memory `feedback_signed_commits.md`. Never mention Claude. Use the per-commit override pattern: `git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "..."`.

**Reference spec:** `docs/specs/2026-05-23-qwen3-smoke-design.md` (commit `3729ff8`).

---

## File Structure

### inferia-worker (`feat/aws-ec2-bootstrap`)
| Path | Action | Responsibility |
|---|---|---|
| `internal/runtime/ollama_pull.go` | create | Pull-after-ready: POST `/api/pull` to local Ollama container; handle stream + non-stream responses; input validation; bounded by `PullTimeout`. |
| `internal/runtime/ollama_pull_test.go` | create | ≥95% line+branch coverage; httptest server; the 12 cases in spec §7.1. |
| `internal/runtime/runtime.go` | modify | Call `ollamaPullIfNeeded` after readiness probe success when recipe name starts with `ollama`. |
| `.github/workflows/docker-publish.yml` | modify | Add `tag_suffix` input to existing `workflow_dispatch`; route through `docker/metadata-action` as a `type=raw` tag. |

### InferiaLLM (`feat/aws-ec2-provisioning`)
| Path | Action | Responsibility |
|---|---|---|
| `scripts/smoke/__init__.py` | create | Package marker. |
| `scripts/smoke/lib.py` | create | `SmokeAPI` httpx client + `wait_until` + `cost_estimate` + typed errors. |
| `scripts/smoke/test_lib.py` | create | ≥95% coverage with respx mocks. |
| `scripts/smoke/local.py` | create | Local scenario orchestrator (10 steps per spec §5.3). |
| `scripts/smoke/aws.py` | create | AWS scenario orchestrator with traps + post-teardown verification. |
| `deploy/compose.worker-local.yml` | create | Sibling worker compose; joins `deploy_inferia-net`; fail-fast env. |
| `Makefile` | modify | `smoke-local`, `smoke-local-up`, `smoke-local-down`, `smoke-aws`, `smoke-aws-dry`. |
| `apps/dashboard/playwright/e2e/qwen3-local-smoke.spec.ts` | create | Local UI smoke; reuses globalSetup. |
| `apps/dashboard/playwright/fixtures/qwen3-smoke-setup.ts` | create | globalSetup helper: mint token, bring up worker compose, wait for ready. |
| `package/pyproject.toml` | modify (optional) | Add `respx` to `[dev]` extras if not present. |

---

## Phase 0 — Pre-flight

### Task 0: Verify both repos clean and on the right branches

**Files:** none

- [ ] **Step 1: Confirm worker branch**

```bash
cd /storage/intern/hooman/work/inferia-worker
git status
git branch --show-current
```
Expected: `feat/aws-ec2-bootstrap`; working tree clean except possibly `.env.bak` (untracked, ignore).

- [ ] **Step 2: Confirm InferiaLLM branch**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git status
git branch --show-current
```
Expected: `feat/aws-ec2-provisioning`; only `package/uv.lock` untracked (already present).

- [ ] **Step 3: Confirm signing key**

```bash
test -f /home/ankit/.ssh/id_ed25519_gh && echo OK
```
Expected: `OK`. If not, stop and ask the user.

---

## Phase 1 — inferia-worker: Ollama pull-after-ready

All work in `/storage/intern/hooman/work/inferia-worker`.

### Task 1: Scaffold `ollama_pull.go` with happy-path test

**Files:**
- Create: `internal/runtime/ollama_pull.go`
- Create: `internal/runtime/ollama_pull_test.go`

- [ ] **Step 1: Write the failing happy-path test**

`internal/runtime/ollama_pull_test.go`:

```go
package runtime

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"
)

func newOllamaServer(t *testing.T, handler http.HandlerFunc) (string, func()) {
	t.Helper()
	srv := httptest.NewServer(handler)
	u, err := url.Parse(srv.URL)
	if err != nil {
		t.Fatalf("parse server url: %v", err)
	}
	return u.Host, srv.Close
}

func TestOllamaPull_HappyPath(t *testing.T) {
	called := 0
	host, stop := newOllamaServer(t, func(w http.ResponseWriter, r *http.Request) {
		called++
		if r.URL.Path != "/api/pull" {
			t.Errorf("path = %s, want /api/pull", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Errorf("method = %s, want POST", r.Method)
		}
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["name"] != "qwen3:0.6b" {
			t.Errorf("name = %v, want qwen3:0.6b", body["name"])
		}
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(map[string]string{"status": "success"})
	})
	defer stop()

	err := ollamaPull(context.Background(), "http://"+host, "qwen3:0.6b", 5*time.Second)
	if err != nil {
		t.Fatalf("ollamaPull returned %v, want nil", err)
	}
	if called != 1 {
		t.Errorf("called = %d, want 1", called)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /storage/intern/hooman/work/inferia-worker
go test ./internal/runtime/ -run TestOllamaPull_HappyPath -v
```
Expected: FAIL — `undefined: ollamaPull`.

- [ ] **Step 3: Write minimal implementation**

`internal/runtime/ollama_pull.go`:

```go
package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// ollamaPull POSTs /api/pull to the local Ollama container at endpoint and
// waits for completion. endpoint is host:port form (no trailing slash, no path).
// Bounded by timeout. Returns an error wrapped with the pull stage context.
func ollamaPull(ctx context.Context, endpoint, model string, timeout time.Duration) error {
	if err := validateOllamaModelName(model); err != nil {
		return fmt.Errorf("ollama pull: %w", err)
	}

	cctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	body, _ := json.Marshal(map[string]any{"name": model, "stream": false})
	req, err := http.NewRequestWithContext(cctx, http.MethodPost,
		strings.TrimRight(endpoint, "/")+"/api/pull", bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("ollama pull: build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("ollama pull: post: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("ollama pull: status=%d body=%s", resp.StatusCode, truncate(raw, 256))
	}

	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("ollama pull: read body: %w", err)
	}
	return checkOllamaPullResponse(raw)
}

// validateOllamaModelName rejects empty, oversized, or shell-meta-bearing names.
func validateOllamaModelName(name string) error {
	if name == "" {
		return fmt.Errorf("model name empty")
	}
	if len(name) > 256 {
		return fmt.Errorf("model name > 256 chars")
	}
	for _, c := range name {
		if c == 0 || strings.ContainsRune(";|$`<>\n\r", c) {
			return fmt.Errorf("model name has forbidden char %q", c)
		}
	}
	return nil
}

// checkOllamaPullResponse handles both JSON-object and NDJSON-stream forms.
func checkOllamaPullResponse(body []byte) error {
	trimmed := bytes.TrimSpace(body)
	if len(trimmed) == 0 {
		return fmt.Errorf("ollama pull: empty response body")
	}
	// Last non-empty line is the terminal status (works for both stream and single-object).
	lines := bytes.Split(trimmed, []byte("\n"))
	var last []byte
	for i := len(lines) - 1; i >= 0; i-- {
		if t := bytes.TrimSpace(lines[i]); len(t) > 0 {
			last = t
			break
		}
	}
	var msg struct {
		Status string `json:"status"`
		Error  string `json:"error"`
	}
	if err := json.Unmarshal(last, &msg); err != nil {
		return fmt.Errorf("ollama pull: decode terminal line: %w", err)
	}
	if msg.Error != "" {
		return fmt.Errorf("ollama pull: server error: %s", msg.Error)
	}
	if msg.Status != "success" {
		return fmt.Errorf("ollama pull: terminal status=%q, want success", msg.Status)
	}
	return nil
}

func truncate(b []byte, n int) string {
	if len(b) <= n {
		return string(b)
	}
	return string(b[:n]) + "…"
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
go test ./internal/runtime/ -run TestOllamaPull_HappyPath -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/runtime/ollama_pull.go internal/runtime/ollama_pull_test.go
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "runtime: ollama pull-after-ready (happy path)

Adds ollamaPull which POSTs /api/pull to the local Ollama container and
waits for the terminal {status:success} message. Input validation
rejects empty / oversized / shell-meta names.

Single happy-path test for now; further cases land in subsequent commits."
```

---

### Task 2: Streaming NDJSON response

**Files:**
- Modify: `internal/runtime/ollama_pull_test.go`

- [ ] **Step 1: Write the failing streaming test**

Append to `ollama_pull_test.go`:

```go
func TestOllamaPull_StreamingNDJSON(t *testing.T) {
	host, stop := newOllamaServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.WriteHeader(http.StatusOK)
		flusher, _ := w.(http.Flusher)
		for _, line := range []string{
			`{"status":"pulling manifest"}`,
			`{"status":"downloading","completed":100,"total":1000}`,
			`{"status":"downloading","completed":800,"total":1000}`,
			`{"status":"verifying sha256 digest"}`,
			`{"status":"success"}`,
			``,
		} {
			_, _ = io.WriteString(w, line+"\n")
			if flusher != nil {
				flusher.Flush()
			}
		}
	})
	defer stop()

	err := ollamaPull(context.Background(), "http://"+host, "qwen3:0.6b", 5*time.Second)
	if err != nil {
		t.Fatalf("ollamaPull returned %v, want nil", err)
	}
}
```

Add `"io"` to the import block at the top if not already present.

- [ ] **Step 2: Run test to verify behavior**

```bash
go test ./internal/runtime/ -run TestOllamaPull_StreamingNDJSON -v
```

Expected: PASS — `checkOllamaPullResponse` already takes the last non-empty line.

- [ ] **Step 3: Commit**

```bash
git add internal/runtime/ollama_pull_test.go
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "runtime: cover ndjson stream form in ollama pull test"
```

---

### Task 3: 5xx retry + 4xx no-retry

**Files:**
- Modify: `internal/runtime/ollama_pull.go`
- Modify: `internal/runtime/ollama_pull_test.go`

- [ ] **Step 1: Write the failing tests**

Append:

```go
func TestOllamaPull_Transient5xxRetried(t *testing.T) {
	var calls int
	host, stop := newOllamaServer(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls == 1 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, `{"status":"success"}`)
	})
	defer stop()

	err := ollamaPull(context.Background(), "http://"+host, "qwen3:0.6b", 5*time.Second)
	if err != nil {
		t.Fatalf("err = %v, want nil after retry", err)
	}
	if calls != 2 {
		t.Errorf("calls = %d, want 2", calls)
	}
}

func TestOllamaPull_Persistent5xxFails(t *testing.T) {
	var calls int
	host, stop := newOllamaServer(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.WriteHeader(http.StatusInternalServerError)
	})
	defer stop()

	err := ollamaPull(context.Background(), "http://"+host, "qwen3:0.6b", 5*time.Second)
	if err == nil {
		t.Fatalf("err = nil, want non-nil")
	}
	if calls != 2 {
		t.Errorf("calls = %d, want 2 (initial + 1 retry)", calls)
	}
}

func TestOllamaPull_4xxNotRetried(t *testing.T) {
	var calls int
	host, stop := newOllamaServer(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.WriteHeader(http.StatusNotFound)
		_, _ = io.WriteString(w, `{"error":"pull model manifest: file does not exist"}`)
	})
	defer stop()

	err := ollamaPull(context.Background(), "http://"+host, "qwen3:0.6b", 5*time.Second)
	if err == nil {
		t.Fatalf("err = nil, want non-nil")
	}
	if calls != 1 {
		t.Errorf("calls = %d, want 1 (no retry on 4xx)", calls)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
go test ./internal/runtime/ -run 'TestOllamaPull_(Transient|Persistent|4xx)' -v
```
Expected: FAIL — current implementation does not retry.

- [ ] **Step 3: Add retry to implementation**

Replace the `resp, err := http.DefaultClient.Do(req)` and surrounding block in `ollamaPull` with a retry loop. The new structure:

```go
	const maxAttempts = 2
	var lastErr error
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		req, err := http.NewRequestWithContext(cctx, http.MethodPost,
			strings.TrimRight(endpoint, "/")+"/api/pull", bytes.NewReader(body))
		if err != nil {
			return fmt.Errorf("ollama pull: build request: %w", err)
		}
		req.Header.Set("Content-Type", "application/json")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			lastErr = fmt.Errorf("ollama pull: post: %w", err)
			if cctx.Err() != nil {
				return lastErr
			}
			continue // network errors are retryable
		}
		raw, rerr := io.ReadAll(resp.Body)
		_ = resp.Body.Close()
		if rerr != nil {
			lastErr = fmt.Errorf("ollama pull: read body: %w", rerr)
			continue
		}
		if resp.StatusCode >= 500 {
			lastErr = fmt.Errorf("ollama pull: status=%d body=%s", resp.StatusCode, truncate(raw, 256))
			continue
		}
		if resp.StatusCode >= 400 {
			return fmt.Errorf("ollama pull: status=%d body=%s", resp.StatusCode, truncate(raw, 256))
		}
		return checkOllamaPullResponse(raw)
	}
	return lastErr
```

(Replace the existing single-shot block. Make sure to remove the now-dead earlier code that built and sent the request.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
go test ./internal/runtime/ -run TestOllamaPull -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/runtime/ollama_pull.go internal/runtime/ollama_pull_test.go
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "runtime: retry once on 5xx, no retry on 4xx in ollama pull"
```

---

### Task 4: Timeout + network-error handling

**Files:**
- Modify: `internal/runtime/ollama_pull_test.go`

- [ ] **Step 1: Write the failing tests**

Append:

```go
func TestOllamaPull_Timeout(t *testing.T) {
	host, stop := newOllamaServer(t, func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(500 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, `{"status":"success"}`)
	})
	defer stop()

	start := time.Now()
	err := ollamaPull(context.Background(), "http://"+host, "qwen3:0.6b", 100*time.Millisecond)
	elapsed := time.Since(start)
	if err == nil {
		t.Fatalf("err = nil, want timeout")
	}
	if elapsed > 400*time.Millisecond {
		t.Errorf("elapsed = %v, want quick timeout < 400ms", elapsed)
	}
}

func TestOllamaPull_NetworkError(t *testing.T) {
	// Server starts and is immediately closed; the URL is unreachable.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {}))
	host := srv.Listener.Addr().String()
	srv.Close()

	err := ollamaPull(context.Background(), "http://"+host, "qwen3:0.6b", 200*time.Millisecond)
	if err == nil {
		t.Fatalf("err = nil, want network error")
	}
}
```

- [ ] **Step 2: Run tests**

```bash
go test ./internal/runtime/ -run 'TestOllamaPull_(Timeout|NetworkError)' -v
```
Expected: PASS — the retry loop already honors `cctx`, and network errors take the retry path then surface `lastErr`.

- [ ] **Step 3: Commit**

```bash
git add internal/runtime/ollama_pull_test.go
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "runtime: cover ollama-pull timeout and network-error paths"
```

---

### Task 5: Input validation cases

**Files:**
- Modify: `internal/runtime/ollama_pull_test.go`

- [ ] **Step 1: Write the failing tests**

Append:

```go
func TestOllamaPull_RejectsEmptyName(t *testing.T) {
	err := ollamaPull(context.Background(), "http://127.0.0.1:1", "", 5*time.Second)
	if err == nil {
		t.Fatalf("err = nil, want validation failure")
	}
}

func TestOllamaPull_RejectsOversizedName(t *testing.T) {
	name := strings.Repeat("a", 257)
	err := ollamaPull(context.Background(), "http://127.0.0.1:1", name, 5*time.Second)
	if err == nil {
		t.Fatalf("err = nil, want validation failure")
	}
}

func TestOllamaPull_RejectsShellMetaName(t *testing.T) {
	for _, n := range []string{
		"qwen3;rm -rf /",
		"qwen3|cat",
		"qwen3`whoami`",
		"qwen3$PATH",
		"qwen3>file",
		"qwen3<file",
		"line1\nline2",
	} {
		err := ollamaPull(context.Background(), "http://127.0.0.1:1", n, 5*time.Second)
		if err == nil {
			t.Errorf("err = nil for name=%q, want validation failure", n)
		}
	}
}
```

Add `"strings"` to imports if not already present.

- [ ] **Step 2: Run tests**

```bash
go test ./internal/runtime/ -run 'TestOllamaPull_Rejects' -v
```
Expected: PASS — `validateOllamaModelName` already covers these.

- [ ] **Step 3: Commit**

```bash
git add internal/runtime/ollama_pull_test.go
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "runtime: cover ollama-pull input validation edges"
```

---

### Task 6: Bad-status terminal line

**Files:**
- Modify: `internal/runtime/ollama_pull_test.go`

- [ ] **Step 1: Write the failing tests**

Append:

```go
func TestOllamaPull_TerminalStatusNotSuccess(t *testing.T) {
	host, stop := newOllamaServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, `{"status":"pulling"}`+"\n"+`{"error":"manifest not found"}`)
	})
	defer stop()
	err := ollamaPull(context.Background(), "http://"+host, "qwen3:0.6b", 5*time.Second)
	if err == nil {
		t.Fatalf("err = nil, want non-nil")
	}
}

func TestOllamaPull_EmptyBody(t *testing.T) {
	host, stop := newOllamaServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		// No body.
	})
	defer stop()
	err := ollamaPull(context.Background(), "http://"+host, "qwen3:0.6b", 5*time.Second)
	if err == nil {
		t.Fatalf("err = nil, want non-nil")
	}
}

func TestOllamaPull_UnparseableBody(t *testing.T) {
	host, stop := newOllamaServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, `not json`)
	})
	defer stop()
	err := ollamaPull(context.Background(), "http://"+host, "qwen3:0.6b", 5*time.Second)
	if err == nil {
		t.Fatalf("err = nil, want non-nil")
	}
}
```

- [ ] **Step 2: Run tests**

```bash
go test ./internal/runtime/ -run 'TestOllamaPull_(Terminal|Empty|Unparseable)' -v
```
Expected: PASS — `checkOllamaPullResponse` handles all three.

- [ ] **Step 3: Coverage check for the file**

```bash
go test -coverprofile=/tmp/ollamapull.cov -covermode=count ./internal/runtime/ -run TestOllamaPull
go tool cover -func=/tmp/ollamapull.cov | grep ollama_pull.go
```
Expected: ≥95% on each function in `ollama_pull.go`. If not, add tests for the uncovered lines before continuing.

- [ ] **Step 4: Commit**

```bash
git add internal/runtime/ollama_pull_test.go
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "runtime: cover ollama-pull response-shape edges; coverage ≥95%"
```

---

### Task 7: Wire `ollamaPull` into runtime.go

**Files:**
- Modify: `internal/runtime/runtime.go`
- Modify: `internal/runtime/ollama_pull.go` (add the runtime-facing wrapper)
- Modify: `internal/runtime/runtime_test.go`

- [ ] **Step 1: Read the readiness section of runtime.go**

```bash
cd /storage/intern/hooman/work/inferia-worker
grep -n "ReadinessProbe\|readiness\|StateRunning\|StateStarting" internal/runtime/runtime.go | head -30
```
Note the exact line in `LoadModel` where the deployment transitions from `StateStarting` to `StateRunning` after the readiness probe succeeds. That's the insertion point.

- [ ] **Step 2: Add the runtime-facing wrapper to `ollama_pull.go`**

Append to `ollama_pull.go`:

```go
// ollamaPullForDeployment runs ollamaPull for an ollama recipe deployment, using
// the model name annotated in the recipe env. No-op for non-ollama recipes.
func ollamaPullForDeployment(ctx context.Context, d *deployment, timeout time.Duration) error {
	if !strings.HasPrefix(d.plan.ContainerName, "inferia-ollama") {
		return nil
	}
	model := d.plan.Env["INFERIA_OLLAMA_MODEL"]
	if model == "" {
		return fmt.Errorf("ollama recipe missing INFERIA_OLLAMA_MODEL")
	}
	endpoint := fmt.Sprintf("http://127.0.0.1:%d", d.hostPort)
	return ollamaPull(ctx, endpoint, model, timeout)
}
```

- [ ] **Step 3: Insert the call in runtime.go**

In `LoadModel`, immediately after the readiness probe succeeds and before transitioning to `StateRunning`, add:

```go
		if err := ollamaPullForDeployment(ctx, dep, r.cfg.PullTimeout); err != nil {
			// Cleanup: stop + remove the container so the next LoadModel starts fresh.
			_ = r.cfg.Docker.Stop(ctx, dep.containerID)
			_ = r.cfg.Docker.Remove(ctx, dep.containerID)
			r.setState(dep, StateFailed)
			return LoadResult{}, fmt.Errorf("ollama pull-after-ready: %w", err)
		}
```

(Adapt names if `dep`/`r.setState`/`r.cfg.Docker.Stop` differ — read the surrounding code and match local conventions.)

- [ ] **Step 4: Add an integration test using `dockerclient/fake`**

Append to `runtime_test.go`:

```go
func TestRuntime_LoadModel_OllamaPullsAfterReady(t *testing.T) {
	// Setup: fake docker that records calls; httptest server impersonating Ollama
	// inside the container at the allocated host port.
	// ... see existing runtime tests for the harness pattern; mirror them.
	// Assertions:
	//   1. A POST /api/pull was received by the fake server.
	//   2. The deployment state is StateRunning after LoadModel returns.
	//   3. If the pull fails, state is StateFailed and Docker.Stop+Remove were called.
	t.Skip("integration-style; flesh out matching the harness in this file")
}
```

> NOTE: The exact harness depends on existing patterns in `runtime_test.go`. Read the existing tests and mirror the dockerclient/fake setup. If the existing harness allows injecting a port allocator + readiness probe, override them to point at an httptest server and assert it was hit. Replace the `t.Skip` with the real test before the next step.

- [ ] **Step 5: Run all runtime tests**

```bash
go test -race ./internal/runtime/... -v
```
Expected: all PASS. Coverage check:

```bash
go test -coverprofile=/tmp/runtime.cov -covermode=count ./internal/runtime/...
go tool cover -func=/tmp/runtime.cov | tail -3
```
Expected: total ≥95% on `ollama_pull.go`; runtime.go should not regress (compare with `git stash; go tool cover ...` baseline if uncertain).

- [ ] **Step 6: Commit**

```bash
git add internal/runtime/ollama_pull.go internal/runtime/runtime.go internal/runtime/runtime_test.go
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "runtime: invoke ollama pull-after-ready for ollama recipes

After readiness probe success, ollama recipes now POST /api/pull for the
model name annotated in INFERIA_OLLAMA_MODEL before transitioning to
StateRunning. On pull failure the container is stopped+removed and state
becomes StateFailed so the next LoadModel starts fresh.

vllm and other non-ollama recipes are unaffected (no-op fast path)."
```

---

## Phase 2 — inferia-worker: CI tag_suffix input

### Task 8: Add `tag_suffix` input to `docker-publish.yml`

**Files:**
- Modify: `.github/workflows/docker-publish.yml`

- [ ] **Step 1: Read current workflow**

```bash
cat .github/workflows/docker-publish.yml
```
Confirm the `on:` block contains `workflow_dispatch:` (with no inputs) and the `docker/metadata-action` step uses a `tags:` multi-line block.

- [ ] **Step 2: Modify the `on:` block**

Replace:

```yaml
  workflow_dispatch:
```

with:

```yaml
  workflow_dispatch:
    inputs:
      tag_suffix:
        description: 'tag for one-off build (e.g. smoke-1748023456)'
        required: false
        type: string
```

- [ ] **Step 3: Modify the metadata-action `tags:` block**

Find the existing `tags: |` list in the `docker/metadata-action` step. Append one line:

```yaml
            type=raw,value=${{ inputs.tag_suffix }},enable=${{ inputs.tag_suffix != '' }}
```

(Indentation matches the surrounding list — same column as `type=raw,value=latest,...`.)

- [ ] **Step 4: Lint the workflow**

```bash
docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest .github/workflows/docker-publish.yml
```
Expected: no errors. If `actionlint` Docker image is unavailable, fall back to `python -c "import yaml; yaml.safe_load(open('.github/workflows/docker-publish.yml'))"` for a basic syntax check.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/docker-publish.yml
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "ci: add tag_suffix input to docker-publish for one-off smoke builds

Lets the smoke-aws orchestrator dispatch a fresh GHCR build for the
worker without cutting a release tag. Existing v* tag trigger and
'latest' semantics unchanged."
```

---

## Phase 3 — InferiaLLM: Smoke helper library

All work in `/storage/intern/hooman/work/InferiaLLM`.

### Task 9: `SmokeAPI` skeleton + login

**Files:**
- Create: `scripts/smoke/__init__.py`
- Create: `scripts/smoke/lib.py`
- Create: `scripts/smoke/test_lib.py`
- Modify: `package/pyproject.toml` (add respx if missing)

- [ ] **Step 1: Confirm respx availability**

```bash
cd /storage/intern/hooman/work/InferiaLLM
grep -n "respx" package/pyproject.toml || echo MISSING
```
If `MISSING`, add `respx>=0.21` to `[project.optional-dependencies].dev` in `package/pyproject.toml`.

- [ ] **Step 2: Create the package marker**

```bash
mkdir -p scripts/smoke
touch scripts/smoke/__init__.py
```

- [ ] **Step 3: Write the failing test**

`scripts/smoke/test_lib.py`:

```python
"""Tests for scripts.smoke.lib — uses respx to mock all HTTP calls."""
from __future__ import annotations

import httpx
import pytest
import respx

from scripts.smoke.lib import (
    APIError,
    SmokeAPI,
)


BASE = "http://test"


@pytest.fixture
def api() -> SmokeAPI:
    return SmokeAPI(base_url=BASE)


@respx.mock
def test_login_stores_token(api: SmokeAPI) -> None:
    respx.post(f"{BASE}/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-123"})
    )
    api.login("admin@example.com", "pw")
    assert api._token == "tok-123"


@respx.mock
def test_login_propagates_4xx(api: SmokeAPI) -> None:
    respx.post(f"{BASE}/v1/auth/login").mock(
        return_value=httpx.Response(401, json={"detail": "bad creds"})
    )
    with pytest.raises(APIError) as exc:
        api.login("admin@example.com", "wrong")
    assert exc.value.status == 401
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
pytest scripts/smoke/test_lib.py -v
```
Expected: FAIL — module does not exist.

- [ ] **Step 5: Write minimal `lib.py`**

`scripts/smoke/lib.py`:

```python
"""HTTP helpers for the Qwen3 smoke scripts.

Public surface mirrors the spec §5.2. Pure Python; tests use respx mocks.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

import httpx


T = TypeVar("T")


class SmokeError(Exception):
    """Base class for all smoke errors."""


class APIError(SmokeError):
    def __init__(self, status: int, body: str, message: str = "") -> None:
        super().__init__(message or f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class SmokeTimeoutError(SmokeError):
    pass


class EmptyResponseError(SmokeError):
    pass


class StreamTruncatedError(SmokeError):
    pass


@dataclass
class SmokeAPI:
    """Thin httpx wrapper used by the smoke scripts."""

    base_url: str
    timeout: float = 30.0
    _token: str | None = field(default=None, init=False)
    _client: httpx.Client | None = field(default=None, init=False)

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
        return self._client

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        kw.setdefault("headers", {}).update(self._auth_headers())
        resp = self._http().request(method, path, **kw)
        if resp.status_code >= 400:
            raise APIError(resp.status_code, resp.text)
        return resp

    # ---- auth ----

    def login(self, email: str, password: str) -> None:
        resp = self._http().post("/v1/auth/login", json={"email": email, "password": password})
        if resp.status_code >= 400:
            raise APIError(resp.status_code, resp.text)
        self._token = resp.json()["access_token"]

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest scripts/smoke/test_lib.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/smoke/ package/pyproject.toml
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "smoke: SmokeAPI skeleton with login + auth bearer

Foundation for the local/aws smoke orchestrators. login() POSTs
/v1/auth/login and stores the access token for subsequent calls.
Typed errors (APIError, SmokeTimeoutError, etc.) defined up front so
later tasks reuse them."
```

---

### Task 10: Pool CRUD

**Files:**
- Modify: `scripts/smoke/lib.py`
- Modify: `scripts/smoke/test_lib.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_lib.py`:

```python
@respx.mock
def test_create_pool_returns_id(api: SmokeAPI) -> None:
    api._token = "t"
    respx.post(f"{BASE}/v1/compute-pools").mock(
        return_value=httpx.Response(200, json={"id": "pool-abc"})
    )
    pid = api.create_pool(provider="worker", name="smoke-local-1")
    assert pid == "pool-abc"


@respx.mock
def test_create_pool_includes_instance_type_metadata(api: SmokeAPI) -> None:
    api._token = "t"
    route = respx.post(f"{BASE}/v1/compute-pools").mock(
        return_value=httpx.Response(200, json={"id": "p"})
    )
    api.create_pool(
        provider="aws",
        name="smoke-aws-1",
        instance_type="g4dn.xlarge",
        metadata={"subnet_id": "subnet-abc", "worker_image_tag": "smoke-1"},
    )
    sent = route.calls.last.request.read()
    assert b"g4dn.xlarge" in sent
    assert b"subnet-abc" in sent


@respx.mock
def test_destroy_pool_idempotent_on_404(api: SmokeAPI) -> None:
    api._token = "t"
    respx.post(f"{BASE}/v1/compute-pools/p1:destroy").mock(
        return_value=httpx.Response(404, json={"detail": "gone"})
    )
    # 404 is tolerated for idempotency.
    api.destroy_pool("p1")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest scripts/smoke/test_lib.py -v -k pool
```
Expected: FAIL — methods missing.

- [ ] **Step 3: Implement create_pool + destroy_pool**

Append to `SmokeAPI`:

```python
    # ---- pool ----

    def create_pool(
        self,
        *,
        provider: str,
        name: str,
        instance_type: str | None = None,
        region: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        body: dict[str, Any] = {"provider": provider, "name": name}
        if instance_type:
            body["instance_type"] = instance_type
        if region:
            body["region"] = region
        if metadata:
            body["metadata"] = metadata
        return self._request("POST", "/v1/compute-pools", json=body).json()["id"]

    def destroy_pool(self, pool_id: str) -> None:
        """Idempotent: 404 is treated as already destroyed."""
        try:
            self._request("POST", f"/v1/compute-pools/{pool_id}:destroy")
        except APIError as e:
            if e.status != 404:
                raise
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest scripts/smoke/test_lib.py -v -k pool
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke/lib.py scripts/smoke/test_lib.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "smoke: SmokeAPI.create_pool + destroy_pool (idempotent on 404)"
```

---

### Task 11: Worker management — mint token, list workers

**Files:**
- Modify: `scripts/smoke/lib.py`
- Modify: `scripts/smoke/test_lib.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
@respx.mock
def test_mint_bootstrap_token(api: SmokeAPI) -> None:
    api._token = "t"
    respx.post(f"{BASE}/v1/admin/workers/mint").mock(
        return_value=httpx.Response(200, json={"token": "bt-xyz", "expires_at": "2099-01-01T00:00:00Z"}),
    )
    r = api.mint_bootstrap_token("pool-1", ttl_hours=1)
    assert r["token"] == "bt-xyz"


@pytest.mark.parametrize("ttl", [0, 25, -1])
def test_mint_bootstrap_token_rejects_bad_ttl(api: SmokeAPI, ttl: int) -> None:
    with pytest.raises(ValueError):
        api.mint_bootstrap_token("pool-1", ttl_hours=ttl)


@respx.mock
def test_list_workers(api: SmokeAPI) -> None:
    api._token = "t"
    respx.get(f"{BASE}/v1/admin/workers").mock(
        return_value=httpx.Response(
            200,
            json={"workers": [{"node_id": "n1", "status": "ready"}]},
        )
    )
    workers = api.list_workers("pool-1")
    assert workers == [{"node_id": "n1", "status": "ready"}]
```

- [ ] **Step 2: Run tests**

```bash
pytest scripts/smoke/test_lib.py -v -k 'mint or list_workers'
```
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `SmokeAPI`:

```python
    # ---- workers ----

    def mint_bootstrap_token(self, pool_id: str, ttl_hours: int) -> dict[str, Any]:
        if not (1 <= ttl_hours <= 24):
            raise ValueError(f"ttl_hours must be 1..24, got {ttl_hours}")
        return self._request(
            "POST",
            "/v1/admin/workers/mint",
            json={"pool_id": pool_id, "ttl_hours": ttl_hours},
        ).json()

    def list_workers(self, pool_id: str) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/admin/workers", params={"pool": pool_id}).json()["workers"]
```

- [ ] **Step 4: Run tests**

```bash
pytest scripts/smoke/test_lib.py -v -k 'mint or list_workers'
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke/lib.py scripts/smoke/test_lib.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "smoke: mint_bootstrap_token + list_workers"
```

---

### Task 12: Deployment CRUD

**Files:**
- Modify: `scripts/smoke/lib.py`
- Modify: `scripts/smoke/test_lib.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
@respx.mock
def test_create_deployment_returns_id(api: SmokeAPI) -> None:
    api._token = "t"
    route = respx.post(f"{BASE}/v1/deployments").mock(
        return_value=httpx.Response(200, json={"deployment_id": "dep-1"})
    )
    did = api.create_deployment(
        pool_id="p1",
        recipe="vllm",
        model_uri="hf://Qwen/Qwen3-0.6B",
        name="smoke-vllm",
        config={"gpu_memory_utilization": 0.5},
    )
    assert did == "dep-1"
    body = route.calls.last.request.read()
    assert b"Qwen3-0.6B" in body
    assert b"gpu_memory_utilization" in body


@respx.mock
def test_delete_deployment_tolerates_404(api: SmokeAPI) -> None:
    api._token = "t"
    respx.delete(f"{BASE}/v1/deployments/dep-1").mock(
        return_value=httpx.Response(404, json={"detail": "gone"})
    )
    api.delete_deployment("dep-1")


@respx.mock
def test_get_deployment(api: SmokeAPI) -> None:
    api._token = "t"
    respx.get(f"{BASE}/v1/deployments/dep-1").mock(
        return_value=httpx.Response(200, json={"id": "dep-1", "state": "running"})
    )
    d = api.get_deployment("dep-1")
    assert d["state"] == "running"
```

- [ ] **Step 2: Run tests**

```bash
pytest scripts/smoke/test_lib.py -v -k deployment
```
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `SmokeAPI`:

```python
    # ---- deployments ----

    def create_deployment(
        self,
        *,
        pool_id: str,
        recipe: str,
        model_uri: str,
        name: str,
        config: dict[str, Any] | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "pool_id": pool_id,
            "recipe": recipe,
            "model_uri": model_uri,
            "name": name,
        }
        if config:
            body["config"] = config
        return self._request("POST", "/v1/deployments", json=body).json()["deployment_id"]

    def delete_deployment(self, deployment_id: str) -> None:
        try:
            self._request("DELETE", f"/v1/deployments/{deployment_id}")
        except APIError as e:
            if e.status != 404:
                raise

    def get_deployment(self, deployment_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/deployments/{deployment_id}").json()
```

- [ ] **Step 4: Run tests**

```bash
pytest scripts/smoke/test_lib.py -v -k deployment
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke/lib.py scripts/smoke/test_lib.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "smoke: deployment create/get/delete on SmokeAPI"
```

---

### Task 13: Chat (non-stream + stream)

**Files:**
- Modify: `scripts/smoke/lib.py`
- Modify: `scripts/smoke/test_lib.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
@respx.mock
def test_chat_non_stream(api: SmokeAPI) -> None:
    api._token = "t"
    respx.post(f"{BASE}/v1/inference/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hello there"}}]},
        )
    )
    assert api.chat("dep-1", "say hi") == "hello there"


@respx.mock
def test_chat_non_stream_empty_raises(api: SmokeAPI) -> None:
    api._token = "t"
    respx.post(f"{BASE}/v1/inference/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": ""}}]},
        )
    )
    with pytest.raises(EmptyResponseError):
        api.chat("dep-1", "say hi")


@respx.mock
def test_chat_stream_concatenates(api: SmokeAPI) -> None:
    api._token = "t"
    body = (
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post(f"{BASE}/v1/inference/chat/completions").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}),
    )
    assert api.chat("dep-1", "hi", stream=True) == "Hello"


@respx.mock
def test_chat_stream_missing_done_raises(api: SmokeAPI) -> None:
    api._token = "t"
    body = 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
    respx.post(f"{BASE}/v1/inference/chat/completions").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}),
    )
    with pytest.raises(StreamTruncatedError):
        api.chat("dep-1", "hi", stream=True)
```

Add imports at the top of the test file:

```python
from scripts.smoke.lib import EmptyResponseError, StreamTruncatedError
```

- [ ] **Step 2: Run tests**

```bash
pytest scripts/smoke/test_lib.py -v -k chat
```
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `SmokeAPI`:

```python
    # ---- chat ----

    def chat(
        self,
        deployment_id: str,
        prompt: str,
        *,
        stream: bool = False,
        timeout: float = 60.0,
    ) -> str:
        body = {
            "deployment_id": deployment_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        if not stream:
            resp = self._http().post(
                "/v1/inference/chat/completions",
                json=body,
                headers=self._auth_headers(),
                timeout=timeout,
            )
            if resp.status_code >= 400:
                raise APIError(resp.status_code, resp.text)
            content = resp.json()["choices"][0]["message"]["content"]
            if not content:
                raise EmptyResponseError("assistant content empty")
            return content

        # Stream path: parse SSE manually so we don't pull in a heavier dep.
        out: list[str] = []
        saw_done = False
        with self._http().stream(
            "POST",
            "/v1/inference/chat/completions",
            json=body,
            headers=self._auth_headers(),
            timeout=timeout,
        ) as resp:
            if resp.status_code >= 400:
                raise APIError(resp.status_code, resp.read().decode())
            for line in resp.iter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    saw_done = True
                    break
                try:
                    import json as _json
                    chunk = _json.loads(payload)
                except Exception:
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                if delta:
                    out.append(delta)
        if not saw_done:
            raise StreamTruncatedError("stream ended without [DONE]")
        full = "".join(out)
        if not full:
            raise EmptyResponseError("stream produced no content")
        return full
```

- [ ] **Step 4: Run tests**

```bash
pytest scripts/smoke/test_lib.py -v -k chat
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke/lib.py scripts/smoke/test_lib.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "smoke: chat completion (non-stream + SSE stream) on SmokeAPI"
```

---

### Task 14: `wait_until` + `cost_estimate`

**Files:**
- Modify: `scripts/smoke/lib.py`
- Modify: `scripts/smoke/test_lib.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_wait_until_returns_first_truthy() -> None:
    from scripts.smoke.lib import wait_until
    calls = {"n": 0}
    def p() -> str | None:
        calls["n"] += 1
        return "ok" if calls["n"] >= 3 else None
    assert wait_until(p, timeout=1.0, interval=0.01) == "ok"
    assert calls["n"] == 3


def test_wait_until_times_out() -> None:
    from scripts.smoke.lib import wait_until
    with pytest.raises(SmokeTimeoutError):
        wait_until(lambda: None, timeout=0.05, interval=0.01)


def test_wait_until_tolerates_503(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.smoke.lib import wait_until
    calls = {"n": 0}
    def p() -> str | None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise APIError(503, "")
        return "ok"
    assert wait_until(p, timeout=1.0, interval=0.01) == "ok"


def test_wait_until_propagates_4xx() -> None:
    from scripts.smoke.lib import wait_until
    def p() -> str | None:
        raise APIError(404, "")
    with pytest.raises(APIError):
        wait_until(p, timeout=1.0, interval=0.01)


def test_cost_estimate() -> None:
    from scripts.smoke.lib import cost_estimate
    s = cost_estimate("g4dn.xlarge", 0.083)
    assert "g4dn.xlarge" in s
    assert "$" in s
```

Add `SmokeTimeoutError` to test imports.

- [ ] **Step 2: Run tests**

```bash
pytest scripts/smoke/test_lib.py -v -k 'wait_until or cost_estimate'
```
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `lib.py` (at module level, not in the class):

```python
INSTANCE_HOURLY_USD = {
    "g4dn.xlarge": 0.526,
    "g5.xlarge": 1.006,
    "g6.xlarge": 0.805,
}


def wait_until(
    predicate: Callable[[], T | None],
    *,
    timeout: float,
    interval: float = 2.0,
    tolerate_status: set[int] = frozenset({503, 504}),
) -> T:
    """Poll `predicate` until it returns truthy or `timeout` elapses.

    APIError with status in `tolerate_status` is swallowed (counts as not-yet).
    Any other APIError propagates. SmokeTimeoutError is raised on deadline.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            v = predicate()
        except APIError as e:
            if e.status not in tolerate_status:
                raise
            v = None
        if v:
            return v
        if time.monotonic() >= deadline:
            raise SmokeTimeoutError(f"timed out after {timeout}s")
        time.sleep(interval)


def cost_estimate(instance_type: str, hours: float) -> str:
    rate = INSTANCE_HOURLY_USD.get(instance_type, 0.0)
    total = rate * hours
    return f"{instance_type} × {hours:.2f}h ≈ ${total:.3f}"
```

- [ ] **Step 4: Run tests**

```bash
pytest scripts/smoke/test_lib.py -v -k 'wait_until or cost_estimate'
```
Expected: PASS.

- [ ] **Step 5: Coverage check**

```bash
pytest --cov=scripts.smoke.lib --cov-report=term-missing scripts/smoke/test_lib.py
```
Expected: ≥95% line+branch on `lib.py`. If anything sub-95%, add a targeted test before continuing.

- [ ] **Step 6: Commit**

```bash
git add scripts/smoke/lib.py scripts/smoke/test_lib.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "smoke: wait_until polling helper + cost_estimate

Final piece of the smoke lib. wait_until tolerates 503/504 but
propagates 4xx; cost_estimate prints a formatted dollar figure for
the AWS spend gate. Line+branch coverage on lib.py ≥95%."
```

---

## Phase 4 — InferiaLLM: Compose + Makefile

### Task 15: `deploy/compose.worker-local.yml`

**Files:**
- Create: `deploy/compose.worker-local.yml`

- [ ] **Step 1: Create the compose file**

`deploy/compose.worker-local.yml`:

```yaml
# Sibling worker compose for the local Qwen3 smoke. Brought up by
# scripts/smoke/local.py after a real bootstrap token has been minted.
#
# Required env (no defaults — docker compose refuses to start otherwise):
#   BOOTSTRAP_TOKEN, POOL_ID, INFERENCE_TOKEN
# Optional:
#   NODE_NAME (default: smoke-local-1)

services:
  worker:
    image: inferia-worker:smoke
    container_name: inferia-worker
    restart: unless-stopped
    environment:
      CONTROL_PLANE_URL:    http://gateway:8000
      BOOTSTRAP_TOKEN:      ${BOOTSTRAP_TOKEN:?required}
      POOL_ID:              ${POOL_ID:?required}
      NODE_NAME:            ${NODE_NAME:-smoke-local-1}
      WORKER_ADVERTISE_URL: http://inferia-worker:8080
      INFERENCE_TOKEN:      ${INFERENCE_TOKEN:?required}
      MODELS_NETWORK:       inferia-models
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:rw
      - worker-state-local:/var/lib/inferia-worker
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    networks: [default, inferia-cp]

volumes:
  worker-state-local:

networks:
  inferia-cp:
    name: deploy_inferia-net
    external: true
```

- [ ] **Step 2: Lint the compose**

```bash
docker compose -f deploy/compose.worker-local.yml config >/dev/null 2>&1 \
  && echo OK \
  || docker compose -f deploy/compose.worker-local.yml config
```
Note: `config` will fail with "required variable BOOTSTRAP_TOKEN is missing" because the env vars are intentionally required. That's the expected behavior — confirm the error message names exactly `BOOTSTRAP_TOKEN`/`POOL_ID`/`INFERENCE_TOKEN`. Set placeholders and rerun:

```bash
BOOTSTRAP_TOKEN=x POOL_ID=x INFERENCE_TOKEN=x \
  docker compose -f deploy/compose.worker-local.yml config >/dev/null && echo OK
```
Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add deploy/compose.worker-local.yml
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "deploy: sibling worker compose for local Qwen3 smoke

Joins deploy_inferia-net so the worker reaches the gateway at
http://gateway:8000 and is reachable at http://inferia-worker:8080.
All bootstrap inputs are required (no placeholder defaults) so the
worker is only brought up after the smoke has minted a real token."
```

---

### Task 16: Makefile targets

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Read existing Makefile**

```bash
head -40 Makefile
grep -n "^[a-z][a-z-]*:" Makefile | head
```
Note the existing target style (tabs, `##` doc comments).

- [ ] **Step 2: Append smoke targets**

Append to the Makefile, preserving existing tab indentation:

```makefile

.PHONY: smoke-local smoke-local-up smoke-local-down smoke-aws smoke-aws-dry

smoke-local-up:    ## bring up unified stack and build worker image (no worker container yet)
	docker compose -f deploy/docker-compose.unified.yml up -d
	docker build -t inferia-worker:smoke ../inferia-worker

smoke-local-down:  ## tear down worker compose + unified
	-docker compose -f deploy/compose.worker-local.yml down -v
	docker compose -f deploy/docker-compose.unified.yml down

smoke-local: smoke-local-up   ## run the local Qwen3 smoke end-to-end
	python -m scripts.smoke.local

smoke-aws-dry:     ## AWS smoke pre-flight only (no spend)
	python -m scripts.smoke.aws --dry-run

smoke-aws:         ## real EC2 AWS smoke; hard 20-min wall clock
	timeout 1200 python -m scripts.smoke.aws --instance-type=g4dn.xlarge
```

- [ ] **Step 3: Verify targets parse**

```bash
make -n smoke-local-up
make -n smoke-aws-dry
```
Expected: each prints the recipe commands without errors.

- [ ] **Step 4: Commit**

```bash
git add Makefile
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "make: smoke-local{,-up,-down} and smoke-aws{,-dry} targets"
```

---

## Phase 5 — InferiaLLM: Smoke orchestrator scripts

### Task 17: `scripts/smoke/local.py`

**Files:**
- Create: `scripts/smoke/local.py`

- [ ] **Step 1: Write the orchestrator**

`scripts/smoke/local.py`:

```python
"""Local Qwen3 smoke orchestrator.

Steps mirror docs/specs/2026-05-23-qwen3-smoke-design.md §5.3. Brings up the
sibling inferia-worker compose after minting a real bootstrap token, then
sequentially deploys Ollama Qwen3 → chats → undeploys → deploys vLLM
Qwen3 → chats → undeploys → tears down.
"""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

from scripts.smoke.lib import APIError, SmokeAPI, SmokeError, SmokeTimeoutError, wait_until


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy" / "compose.worker-local.yml"
UNIFIED_FILE = REPO_ROOT / "deploy" / "docker-compose.unified.yml"

GATEWAY_URL = os.environ.get("SMOKE_GATEWAY_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("SMOKE_ADMIN_EMAIL", "admin@inferia.local")
ADMIN_PASSWORD = os.environ.get("SMOKE_ADMIN_PASSWORD", "admin")


def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, check=True, text=True, **kw)


def ensure_preconditions() -> None:
    # Image must exist.
    img = subprocess.run(
        ["docker", "image", "inspect", "inferia-worker:smoke"],
        capture_output=True,
    )
    if img.returncode != 0:
        sys.exit("inferia-worker:smoke image not found. Run `make smoke-local-up` first.")
    # No worker container with our fixed name.
    existing = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}", "--filter", "name=^inferia-worker$"],
        capture_output=True, text=True,
    )
    if existing.stdout.strip():
        sys.exit("A container named 'inferia-worker' already exists. Remove it and retry.")


def deploy_and_chat(
    api: SmokeAPI, *, pool_id: str, recipe: str, model_uri: str, config: dict | None,
    ready_timeout: float,
) -> None:
    name = f"smoke-{recipe}-{uuid.uuid4().hex[:6]}"
    dep_id = api.create_deployment(
        pool_id=pool_id, recipe=recipe, model_uri=model_uri, name=name, config=config,
    )
    print(f"  deployment {dep_id} created; waiting for running...")
    try:
        wait_until(
            lambda: api.get_deployment(dep_id) if api.get_deployment(dep_id).get("state") == "running" else None,
            timeout=ready_timeout, interval=4.0,
        )
        out = api.chat(dep_id, "Say hello in one short sentence.")
        if not out.strip():
            raise SmokeError(f"{recipe}: empty chat response")
        print(f"  {recipe} OK — {out!r}")
    finally:
        api.delete_deployment(dep_id)
        # Let VRAM drain before the next deploy.
        time.sleep(8)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--keep-on-fail", action="store_true")
    p.add_argument("--engines", default="ollama,vllm", help="comma-separated list")
    args = p.parse_args()

    ensure_preconditions()

    api = SmokeAPI(base_url=GATEWAY_URL)
    api.login(ADMIN_EMAIL, ADMIN_PASSWORD)

    pool_name = f"smoke-local-{uuid.uuid4().hex[:6]}"
    pool_id = api.create_pool(provider="worker", name=pool_name)
    print(f"pool {pool_id} ({pool_name}) created")

    bootstrap = api.mint_bootstrap_token(pool_id, ttl_hours=1)
    inf_tok = secrets.token_hex(32)

    env = os.environ.copy()
    env.update(
        BOOTSTRAP_TOKEN=bootstrap["token"],
        POOL_ID=pool_id,
        INFERENCE_TOKEN=inf_tok,
    )

    fail = False
    try:
        # Tell control plane which inference token to use for this pool.
        # (Endpoint name is provisional; adapt to actual API in integration.)
        try:
            api._request(
                "POST", f"/v1/compute-pools/{pool_id}/inference-token",
                json={"token": inf_tok},
            )
        except APIError as e:
            if e.status != 404:
                raise

        run(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"], env=env)

        print("waiting for worker to register as ready...")
        wait_until(
            lambda: api.list_workers(pool_id)
            if any(w.get("status") == "ready" for w in api.list_workers(pool_id))
            else None,
            timeout=60.0, interval=2.0,
        )
        print("worker ready")

        for engine in args.engines.split(","):
            engine = engine.strip()
            if engine == "ollama":
                deploy_and_chat(
                    api, pool_id=pool_id, recipe="ollama",
                    model_uri="ollama://qwen3:0.6b",
                    config=None, ready_timeout=180.0,
                )
            elif engine == "vllm":
                deploy_and_chat(
                    api, pool_id=pool_id, recipe="vllm",
                    model_uri="hf://Qwen/Qwen3-0.6B",
                    config={
                        "gpu_memory_utilization": 0.5,
                        "max_model_len": 4096,
                        "dtype": "bfloat16",
                    },
                    ready_timeout=300.0,
                )
            else:
                raise SmokeError(f"unknown engine {engine}")
    except (SmokeError, SmokeTimeoutError, APIError, subprocess.CalledProcessError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        fail = True

    if fail and args.keep_on_fail:
        print("--keep-on-fail set; leaving stack up", file=sys.stderr)
        return 1

    print("tearing down worker compose and pool...")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
        check=False,
    )
    api.destroy_pool(pool_id)
    api.close()
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Syntax check**

```bash
python -m py_compile scripts/smoke/local.py
```
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke/local.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "smoke: local Qwen3 orchestrator script

Sequences login → create pool → mint token → start worker compose →
wait ready → deploy ollama Qwen3 → chat → undeploy → deploy vllm Qwen3
(gpu_memory_utilization=0.5) → chat → undeploy → teardown.

--keep-on-fail skips teardown for post-mortem."
```

---

### Task 18: `scripts/smoke/aws.py`

**Files:**
- Create: `scripts/smoke/aws.py`

- [ ] **Step 1: Write the orchestrator**

`scripts/smoke/aws.py`:

```python
"""AWS Qwen3 smoke orchestrator with defense-in-depth teardown.

Layers (per spec §7.3):
  1. Pre-flight reject of pre-existing smoke-aws-* pools.
  2. try/finally + atexit destroy.
  3. Cost printout + 5s Ctrl-C window.
  4. Wall-clock guard via outer Makefile timeout(1).
  5. boto3 post-teardown verification.
"""
from __future__ import annotations

import argparse
import atexit
import os
import signal
import subprocess
import sys
import time
import uuid

from scripts.smoke.lib import (
    APIError,
    SmokeAPI,
    SmokeError,
    SmokeTimeoutError,
    cost_estimate,
    wait_until,
)


GATEWAY_URL = os.environ.get("SMOKE_GATEWAY_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("SMOKE_ADMIN_EMAIL", "admin@inferia.local")
ADMIN_PASSWORD = os.environ.get("SMOKE_ADMIN_PASSWORD", "admin")
WORKER_REPO = os.environ.get("SMOKE_WORKER_REPO", "inferia/inferia-worker")


def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, check=True, text=True, **kw)


def trigger_ghcr_build(tag_suffix: str) -> None:
    """Dispatch the docker-publish workflow and block until success."""
    run([
        "gh", "workflow", "run", "docker-publish.yml",
        "-R", WORKER_REPO,
        "-f", f"tag_suffix={tag_suffix}",
    ])
    # Wait for the most recent run to complete.
    deadline = time.monotonic() + 20 * 60
    while time.monotonic() < deadline:
        out = subprocess.run(
            ["gh", "run", "list", "-R", WORKER_REPO,
             "--workflow", "docker-publish.yml", "--limit", "1",
             "--json", "status,conclusion"],
            capture_output=True, text=True, check=True,
        )
        if '"status":"completed"' in out.stdout:
            if '"conclusion":"success"' in out.stdout:
                return
            sys.exit(f"GHCR build failed: {out.stdout}")
        time.sleep(20)
    sys.exit("GHCR build timed out (20 min)")


def preflight(api: SmokeAPI) -> dict:
    """Verify AWS provider is configured and no stale smoke pools exist."""
    # Provider config check.
    try:
        providers = api._request("GET", "/v1/providers").json().get("providers", [])
    except APIError as e:
        sys.exit(f"unable to list providers: {e}")
    aws = next((p for p in providers if p.get("provider_type") == "aws" and p.get("configured")), None)
    if not aws:
        sys.exit("AWS provider not configured. Configure it in Settings → Providers first.")
    # Stale pool check.
    try:
        pools = api._request("GET", "/v1/compute-pools").json().get("pools", [])
    except APIError as e:
        sys.exit(f"unable to list pools: {e}")
    stale = [p for p in pools if str(p.get("name", "")).startswith("smoke-aws-")]
    if stale:
        names = ", ".join(p["name"] for p in stale)
        sys.exit(f"pre-existing smoke pool(s) found: {names}. Destroy them first.")
    return aws


def verify_no_running_instances(pool_id: str) -> None:
    """boto3 describe-instances; fail if anything is running/pending for our pool."""
    try:
        import boto3
    except ImportError:
        print("(boto3 not installed; skipping post-teardown verification)", file=sys.stderr)
        return
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    ec2 = boto3.client("ec2", region_name=region)
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:InferiaPoolId", "Values": [pool_id]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping"]},
        ],
    )
    leftover = [
        i["InstanceId"]
        for r in resp.get("Reservations", [])
        for i in r.get("Instances", [])
    ]
    if leftover:
        sys.exit(f"INSTANCES STILL LIVE after teardown: {leftover}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--instance-type", default="g4dn.xlarge")
    p.add_argument("--region", default=None)
    p.add_argument("--worker-image-tag", default=None,
                   help="if set, skip GHCR build and reuse this tag")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--keep-on-fail", action="store_true")
    args = p.parse_args()

    api = SmokeAPI(base_url=GATEWAY_URL)
    api.login(ADMIN_EMAIL, ADMIN_PASSWORD)

    aws = preflight(api)
    if args.dry_run:
        print("dry-run OK: AWS provider configured, no stale pools")
        return 0

    # Cost gate.
    print(cost_estimate(args.instance_type, hours=1/6))
    if not os.environ.get("SMOKE_NO_CONFIRM"):
        print("Ctrl-C within 5s to abort...", flush=True)
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            sys.exit("aborted")

    tag = args.worker_image_tag or f"smoke-{int(time.time())}"
    if not args.worker_image_tag:
        trigger_ghcr_build(tag)

    pool_id: str | None = None

    def teardown() -> None:
        if pool_id is None:
            return
        try:
            api.destroy_pool(pool_id)
        except Exception as e:
            print(f"teardown destroy_pool failed: {e}", file=sys.stderr)
        try:
            verify_no_running_instances(pool_id)
        except SystemExit as e:
            print(str(e), file=sys.stderr)

    atexit.register(teardown)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit("SIGTERM"))
    signal.signal(signal.SIGINT, lambda *_: sys.exit("SIGINT"))

    fail = False
    try:
        ts = int(time.time())
        pool_id = api.create_pool(
            provider="aws",
            name=f"smoke-aws-{ts}-{uuid.uuid4().hex[:4]}",
            instance_type=args.instance_type,
            region=args.region or aws.get("default_region"),
            metadata={
                **(aws.get("metadata") or {}),
                "worker_image_tag": tag,
            },
        )
        print(f"pool {pool_id} created; waiting for pulumi succeeded...")
        wait_until(
            lambda: api._request("GET", f"/v1/compute-pools/{pool_id}").json()
                    if api._request("GET", f"/v1/compute-pools/{pool_id}").json().get("pulumi_state") == "succeeded"
                    else None,
            timeout=300.0, interval=10.0,
        )
        print("pulumi succeeded; waiting for worker register...")
        wait_until(
            lambda: api.list_workers(pool_id)
                    if any(w.get("status") == "ready" for w in api.list_workers(pool_id))
                    else None,
            timeout=180.0, interval=5.0,
        )
        # Reuse local.deploy_and_chat by importing it.
        from scripts.smoke.local import deploy_and_chat
        deploy_and_chat(
            api, pool_id=pool_id, recipe="ollama",
            model_uri="ollama://qwen3:0.6b", config=None, ready_timeout=240.0,
        )
        deploy_and_chat(
            api, pool_id=pool_id, recipe="vllm",
            model_uri="hf://Qwen/Qwen3-0.6B",
            config={"gpu_memory_utilization": 0.85, "max_model_len": 4096, "dtype": "bfloat16"},
            ready_timeout=360.0,
        )
    except (SmokeError, SmokeTimeoutError, APIError, subprocess.CalledProcessError) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        fail = True

    api.close()
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Syntax check**

```bash
python -m py_compile scripts/smoke/aws.py
```
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke/aws.py
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "smoke: AWS Qwen3 orchestrator with traps + post-teardown verification

Triggers a one-off GHCR build of inferia-worker via gh workflow run,
creates a Pulumi-backed AWS pool, waits for pulumi+worker ready,
sequentially deploys ollama then vllm Qwen3-0.6B, chats each, then
destroys the pool and verifies no EC2 instances remain.

Six teardown layers: pre-flight stale-pool reject, atexit destroy,
SIGTERM/SIGINT handlers, cost gate, post-teardown boto3 verification,
outer Makefile timeout(1200)."
```

---

## Phase 6 — InferiaLLM: Playwright spec

### Task 19: globalSetup fixture

**Files:**
- Create: `apps/dashboard/playwright/fixtures/qwen3-smoke-setup.ts`

- [ ] **Step 1: Read existing Playwright config**

```bash
cat apps/dashboard/playwright.config.ts 2>/dev/null | head -40
ls apps/dashboard/playwright/ 2>/dev/null
```
Note `globalSetup` / `globalTeardown` paths and the auth fixture pattern. Match them.

- [ ] **Step 2: Create the fixture**

`apps/dashboard/playwright/fixtures/qwen3-smoke-setup.ts`:

```typescript
import { execSync } from "node:child_process";
import path from "node:path";

const REPO_ROOT = path.resolve(__dirname, "../../../..");
const COMPOSE = path.join(REPO_ROOT, "deploy", "compose.worker-local.yml");
const GATEWAY = process.env.PLAYWRIGHT_GATEWAY_URL ?? "http://localhost:8000";
const ADMIN_EMAIL = process.env.PLAYWRIGHT_ADMIN_EMAIL ?? "admin@inferia.local";
const ADMIN_PASSWORD = process.env.PLAYWRIGHT_ADMIN_PASSWORD ?? "admin";

interface SetupResult {
  poolId: string;
  poolName: string;
  workerNodeName: string;
}

async function api<T>(token: string | null, method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  const res = await fetch(`${GATEWAY}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

export async function setupLocalWorker(): Promise<SetupResult> {
  const auth = await api<{ access_token: string }>(null, "POST", "/v1/auth/login", {
    email: ADMIN_EMAIL, password: ADMIN_PASSWORD,
  });
  const token = auth.access_token;
  const poolName = `pw-smoke-${Date.now().toString(36)}`;
  const pool = await api<{ id: string }>(token, "POST", "/v1/compute-pools", {
    provider: "worker", name: poolName,
  });
  const bs = await api<{ token: string }>(token, "POST", "/v1/admin/workers/mint", {
    pool_id: pool.id, ttl_hours: 1,
  });
  const env = {
    ...process.env,
    BOOTSTRAP_TOKEN: bs.token,
    POOL_ID: pool.id,
    INFERENCE_TOKEN: require("node:crypto").randomBytes(32).toString("hex"),
    NODE_NAME: "pw-smoke-1",
  };
  execSync(`docker compose -f ${COMPOSE} up -d`, { env, stdio: "inherit" });
  // Wait for worker ready (≤ 60 s).
  const deadline = Date.now() + 60_000;
  for (;;) {
    const w = await api<{ workers: Array<{ status: string }> }>(
      token, "GET", `/v1/admin/workers?pool=${pool.id}`,
    );
    if (w.workers.some(x => x.status === "ready")) break;
    if (Date.now() > deadline) throw new Error("worker did not become ready in 60s");
    await new Promise(r => setTimeout(r, 2_000));
  }
  return { poolId: pool.id, poolName, workerNodeName: "pw-smoke-1" };
}

export async function teardownLocalWorker(state: SetupResult): Promise<void> {
  try {
    execSync(`docker compose -f ${COMPOSE} down -v`, { stdio: "inherit" });
  } catch (e) {
    // log and continue
    console.error("compose down failed", e);
  }
  // Pool destroy via API.
  try {
    const auth = await api<{ access_token: string }>(null, "POST", "/v1/auth/login", {
      email: ADMIN_EMAIL, password: ADMIN_PASSWORD,
    });
    await api(auth.access_token, "POST", `/v1/compute-pools/${state.poolId}:destroy`);
  } catch (e) {
    console.error("pool destroy failed", e);
  }
}
```

- [ ] **Step 3: Type-check the fixture**

```bash
cd apps/dashboard
npx tsc --noEmit -p tsconfig.json
```
Expected: no errors in the fixture file.

- [ ] **Step 4: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add apps/dashboard/playwright/fixtures/qwen3-smoke-setup.ts
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "playwright: fixture to bring up + tear down local worker for qwen3 smoke"
```

---

### Task 20: Playwright spec

**Files:**
- Create: `apps/dashboard/playwright/e2e/qwen3-local-smoke.spec.ts`

- [ ] **Step 1: Read existing spec patterns**

```bash
ls apps/dashboard/playwright/e2e/ 2>/dev/null
cat apps/dashboard/playwright/e2e/*.spec.ts 2>/dev/null | head -60
```
Note the imports + page-object patterns. The new spec should mirror them.

- [ ] **Step 2: Create the spec**

`apps/dashboard/playwright/e2e/qwen3-local-smoke.spec.ts`:

```typescript
import { expect, test } from "@playwright/test";
import { setupLocalWorker, teardownLocalWorker } from "../fixtures/qwen3-smoke-setup";

type Setup = Awaited<ReturnType<typeof setupLocalWorker>>;
let state: Setup;

test.beforeAll(async () => { state = await setupLocalWorker(); });
test.afterAll(async () => { if (state) await teardownLocalWorker(state); });

test.describe.configure({ mode: "serial" });

test("ollama Qwen3 deployment is chattable from sandbox", async ({ page }) => {
  test.setTimeout(5 * 60_000);

  // Login.
  await page.goto("/login");
  await page.getByLabel(/email/i).fill(process.env.PLAYWRIGHT_ADMIN_EMAIL ?? "admin@inferia.local");
  await page.getByLabel(/password/i).fill(process.env.PLAYWRIGHT_ADMIN_PASSWORD ?? "admin");
  await page.getByRole("button", { name: /sign in|login/i }).click();
  await expect(page).toHaveURL(/\/overview|\/dashboard/);

  // Compute → Pools → our pool.
  await page.getByRole("link", { name: /compute/i }).click();
  await page.getByText(state.poolName).click();
  await expect(page.getByText(state.workerNodeName)).toBeVisible({ timeout: 30_000 });

  // Deployments → New → Ollama → qwen3:0.6b.
  await page.getByRole("link", { name: /deployments/i }).click();
  await page.getByRole("button", { name: /new deployment/i }).click();
  await page.getByText(/inference/i).first().click();
  await page.getByText(/ollama/i, { exact: false }).first().click();
  await page.getByPlaceholder(/model name|search/i).fill("qwen3:0.6b");
  await page.getByText(/qwen3.*0\.6/i).first().click();
  await page.getByText(state.poolName).click();
  await page.getByRole("button", { name: /deploy/i }).click();

  // Wait for the deployment row to show Running.
  await expect(page.getByText("Running")).toBeVisible({ timeout: 3 * 60_000 });

  // Sandbox.
  await page.getByRole("link", { name: /sandbox/i }).click();
  // Sandbox auto-selects a deployment; if there's a dropdown, pick the qwen3 one.
  const input = page.getByPlaceholder(/type a message|ask/i);
  await input.fill("say hello in one short sentence");
  await page.getByRole("button", { name: /send/i }).click();

  // Assert non-empty assistant content lands in the chat area.
  const assistantArea = page.locator('[data-role="assistant"], .assistant-message').first();
  await expect(assistantArea).toContainText(/\w+/, { timeout: 90_000 });
});
```

- [ ] **Step 3: Type-check**

```bash
cd apps/dashboard
npx tsc --noEmit -p tsconfig.json
```
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add apps/dashboard/playwright/e2e/qwen3-local-smoke.spec.ts
git -c user.signingkey=/home/ankit/.ssh/id_ed25519_gh commit -S -m "playwright: qwen3 local smoke spec — ollama deployment + sandbox chat

Drives the UI end-to-end: login → Compute pool selection → New
Deployment (ollama qwen3:0.6b) → wait Running → Sandbox chat. Assertion
is non-empty assistant content within 90 s. AWS scenario is left to the
bash smoke; Playwright is local-only."
```

---

## Phase 7 — End-to-end verification

### Task 21: Run local smoke; fix bugs found

**Files:** none initially; possibly edits as bugs surface

- [ ] **Step 1: Build worker image with the new ollama-pull patch**

```bash
cd /storage/intern/hooman/work/InferiaLLM
make smoke-local-up
```
Expected: unified stack up + `inferia-worker:smoke` built from `../inferia-worker`.

- [ ] **Step 2: Run the local smoke**

```bash
make smoke-local
```
Expected: exit 0 with `ollama OK — ...` and `vllm OK — ...` lines.

- [ ] **Step 3: If any step fails**

Capture logs:

```bash
docker logs inferia-worker --tail 200
docker logs deploy-orchestration-1 --tail 200
docker logs deploy-gateway-1 --tail 200
```

Diagnose. Common buckets:
- Inference token / pool-token endpoint name differs from `/v1/compute-pools/{id}/inference-token` → read the real route in `package/src/inferia/services/orchestration/api/admin_workers.py`, fix `local.py`.
- Worker container can't resolve `gateway` → confirm `deploy_inferia-net` is the actual network name (`docker network ls`).
- Ollama recipe ContainerName prefix differs from `inferia-ollama` → read `internal/runtime/recipes/others.go`, adapt `ollamaPullForDeployment`.

Apply the smallest fix, document any non-obvious one in `CLAUDE.md`'s Mistakes Log per the user-global instruction, and commit signed.

- [ ] **Step 4: Run Playwright spec**

```bash
cd apps/dashboard
npx playwright test --grep qwen3-local-smoke
```
Expected: 1 passing in ~3 min.

- [ ] **Step 5: Tear down**

```bash
cd /storage/intern/hooman/work/InferiaLLM
make smoke-local-down
```

- [ ] **Step 6: Commit any bugfixes**

If you patched anything, commit per-fix with descriptive messages, signed.

---

### Task 22: Run AWS smoke; fix bugs found

**Files:** none initially

- [ ] **Step 1: Confirm AWS provider is configured**

```bash
make smoke-aws-dry
```
Expected: `dry-run OK: AWS provider configured, no stale pools`. If not, configure AWS in Settings → Providers via the UI first, then retry.

- [ ] **Step 2: Confirm `gh` auth**

```bash
gh auth status
```
Expected: logged in with `workflow` scope. If `workflow` is missing: `gh auth refresh --scopes workflow,write:packages`.

- [ ] **Step 3: Run the AWS smoke**

```bash
SMOKE_NO_CONFIRM=1 make smoke-aws
```
Expected: ~5–10 min run, exit 0, pool destroyed at end, `INSTANCES STILL LIVE` not printed.

- [ ] **Step 4: If any step fails**

Logs:

```bash
gh run view -R inferia/inferia-worker --log
```

EC2 cloud-init:

```bash
aws ssm start-session --target i-<instance-id>
sudo journalctl -u cloud-final --no-pager | tail -200
```

Common buckets:
- GHCR build fails on the `tag_suffix` form → re-check the YAML diff from Task 8.
- Pulumi state location not writable → confirm the orchestration container has `/var/lib/inferia/pulumi-state` mounted (per existing AWS Pulumi spec).
- Worker on EC2 can't reach the control plane → SG inbound rules vs control plane CIDR (operator-config).

Fix smallest cause, document any non-obvious one in CLAUDE.md Mistakes Log, commit signed.

- [ ] **Step 5: Verify teardown**

```bash
aws ec2 describe-instances --filters "Name=tag:InferiaSmoke,Values=true" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text
```
Expected: empty output, or only `terminated` states.

---

### Task 23: Final commit + handoff

**Files:** none

- [ ] **Step 1: Sanity log**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git log --oneline feat/aws-ec2-provisioning ^origin/feat/aws-ec2-provisioning
cd /storage/intern/hooman/work/inferia-worker
git log --oneline feat/aws-ec2-bootstrap ^origin/feat/aws-ec2-bootstrap
```
All commits should be signed with the `_gh` key and have no Claude attribution.

- [ ] **Step 2: Verify signatures**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git log --show-signature -10 2>&1 | grep -c "Good \"git\" signature"
```
Expected: equals the count of new commits.

- [ ] **Step 3: Hand back**

Print to the user:
- Number of commits in each repo (not pushed).
- Local smoke result (PASS / FAIL).
- AWS smoke result (PASS / FAIL) and total time / approximate cost.
- Any bugs found + their fixes summarised in 1-2 lines each.

Do **not** push and do **not** open PRs.

---

## Self-review

**1. Spec coverage:** every numbered item in §3 of the spec maps to a task here (worker pull patch → Tasks 1-7; CI input → Task 8; smoke lib → Tasks 9-14; compose → Task 15; Makefile → Task 16; orchestrators → Tasks 17-18; Playwright → Tasks 19-20). Verification covered by Tasks 21-23.

**2. Placeholders:** the Playwright fixture references `process.env.PLAYWRIGHT_ADMIN_EMAIL` defaults; that's explicit defaults, not a placeholder. The integration test in Task 7 contains an explicit `t.Skip` with a note instructing the engineer to mirror the existing harness — that's the one allowed gap because the harness lives in code I can't quote verbatim in advance.

**3. Type consistency:** `SmokeAPI.create_deployment` returns `str` (deployment id) everywhere it's called. `wait_until` signature is consistent. `cost_estimate` signature matches the test that imports it. The `state` shape in Playwright fixture is shared between setup + teardown.

If anything in Task 7's `runtime_test.go` integration block needs to be more concrete than the engineer can synthesise from the existing harness, ask for clarification rather than skip silently.
