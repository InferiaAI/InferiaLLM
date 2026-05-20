# AWS EC2 worker provisioning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision real AWS EC2 GPU instances from the InferiaLLM control plane, auto-install `inferia-worker` via cloud-init pulling from GHCR, and have the worker register itself with a one-shot bootstrap token.

**Architecture:** Two repos. The Go worker (`inferia-worker`) gains a `cloudenv` package that probes IMDSv2 at boot and threads runtime-env metadata through its register call and Hello frame. The Python control plane (`InferiaLLM`) gets a real `AWSAdapter`, a `bootstrap_builder` that renders shell-safe user-data, and a `worker_bootstrap_tokens` table with atomic single-use consumption.

**Tech Stack:** Go 1.26 (worker), Python 3.10-3.12 + FastAPI + asyncpg (control plane), boto3 (mocked in tests), Docker Buildx (multi-arch), GitHub Actions + GHCR.

**Spec:** `docs/specs/2026-05-20-aws-ec2-worker-provisioning.md` (commit `0ce97db`).

**Repos:**
- A: `/storage/intern/hooman/work/inferia-worker` — Go
- B: `/storage/intern/hooman/work/InferiaLLM` — Python

**Commit convention:** Every commit MUST be signed and MUST NOT mention Claude in the body. Use whatever signing key your local git config provides; if your environment requires the override workaround documented in memory, apply it per-commit.

**Order rationale:** Worker side first (Tasks 1-8) because the CP changes (Tasks 9-15) depend on knowing the wire shape of the register body. CP changes are bottom-up: schema → auth helpers → endpoint → adapter dependencies → adapter rewrite.

---

## File Structure

### Repo A — inferia-worker (Go)

| File | Status | Responsibility |
|---|---|---|
| `internal/cloudenv/detect.go` | NEW | `Detect() RuntimeInfo` — IMDSv2 probe + env overrides + caching |
| `internal/cloudenv/detect_test.go` | NEW | Unit tests for Detect using `httptest.Server` |
| `internal/control/bootstrap.go` | MOD | Register POST body carries `runtime_env`, `instance_id`, `region`, `availability_zone` |
| `internal/control/bootstrap_test.go` | MOD | Tests for the new fields |
| `internal/control/protocol.go` | MOD | `HelloBody` carries same four fields |
| `internal/control/channel.go` | MOD | Hello sender threads the fields |
| `internal/control/channel_test.go` | MOD | Hello-frame test for new fields |
| `cmd/worker/main.go` | MOD | Wire `cloudenv.Detect()` once, pass into bootstrap + channel |
| `Dockerfile` | MOD | `GOARCH=amd64` → `GOARCH=${TARGETARCH}` for multi-arch buildx |
| `.github/workflows/test.yml` | NEW | go vet / test / build on PR + main |
| `.github/workflows/docker-publish.yml` | NEW | Multi-arch GHCR push on tag + workflow_dispatch |

### Repo B — InferiaLLM (Python)

| File | Status | Responsibility |
|---|---|---|
| `package/src/inferia/infra/schema/migrations/20260520_add_worker_bootstrap_tokens.sql` | NEW | `worker_bootstrap_tokens` table |
| `package/src/inferia/services/orchestration/services/worker_controller/auth.py` | MOD | `mint_bootstrap_token`, `consume_bootstrap_token` |
| `package/src/inferia/services/orchestration/services/worker_controller/test_auth.py` | MOD | Token mint/consume/race/expiry tests |
| `package/src/inferia/services/orchestration/api/workers.py` | MOD | Register endpoint accepts `bootstrap_token` + cloud-env fields |
| `package/src/inferia/services/orchestration/api/test_workers.py` | MOD | Register-endpoint tests |
| `package/src/inferia/common/runtime_env.py` | NEW | CP-side telemetry helper (`detect_runtime_env`) |
| `package/src/inferia/common/tests/test_runtime_env.py` | NEW | Helper tests |
| `package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/bootstrap_builder.py` | NEW | Renders user-data; shell-safe interpolation |
| `package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_bootstrap_builder.py` | NEW | Builder tests |
| `package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/aws_adapter.py` | REWRITE | Real `AWSAdapter` with boto3 |
| `package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_aws_adapter.py` | NEW | Adapter tests (boto3 mocked) |
| `package/src/inferia/services/orchestration/config.py` | MOD | Add `worker_image`, `worker_image_tag`, `bootstrap_token_ttl_seconds` config |

---

# Tasks

## Task 1: Scaffold `internal/cloudenv` package with env-override Detect (Repo A)

**Files:**
- Create: `inferia-worker/internal/cloudenv/detect.go`
- Create: `inferia-worker/internal/cloudenv/detect_test.go`

- [ ] **Step 1: Write the failing test for env-override path**

```go
// inferia-worker/internal/cloudenv/detect_test.go
package cloudenv

import (
	"os"
	"testing"
)

func TestDetect_EnvOverrideSetsKind(t *testing.T) {
	t.Setenv("INFERIA_RUNTIME_ENV", "aws-ec2")
	t.Setenv("INFERIA_INSTANCE_ID", "i-test-1234")
	t.Setenv("INFERIA_REGION", "us-east-1")
	t.Setenv("INFERIA_AZ", "us-east-1a")
	// Force IMDS path off so we don't depend on real network.
	t.Setenv("INFERIA_CLOUDENV_IMDS_URL", "http://127.0.0.1:1") // unreachable

	got := detectFresh() // bypasses cache for tests
	if got.Kind != KindAWSEC2 {
		t.Fatalf("Kind = %q, want %q", got.Kind, KindAWSEC2)
	}
	if got.InstanceID != "i-test-1234" {
		t.Errorf("InstanceID = %q", got.InstanceID)
	}
	if got.Region != "us-east-1" {
		t.Errorf("Region = %q", got.Region)
	}
	if got.AvailabilityZone != "us-east-1a" {
		t.Errorf("AZ = %q", got.AvailabilityZone)
	}
	_ = os.Getenv // keep "os" import alive
}

func TestDetect_NoEnvNoIMDSReturnsLocal(t *testing.T) {
	t.Setenv("INFERIA_RUNTIME_ENV", "")
	t.Setenv("INFERIA_CLOUDENV_IMDS_URL", "http://127.0.0.1:1") // unreachable
	got := detectFresh()
	if got.Kind != KindLocal {
		t.Fatalf("Kind = %q, want %q", got.Kind, KindLocal)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /storage/intern/hooman/work/inferia-worker && go test ./internal/cloudenv/...`
Expected: build failure, "no Go files in" / undefined `Kind`, `detectFresh`, etc.

- [ ] **Step 3: Write minimal implementation**

```go
// inferia-worker/internal/cloudenv/detect.go
//
// Package cloudenv detects the runtime environment the worker is running in
// (AWS EC2, local, etc) and exposes the result to bootstrap + control packages.
// IMDSv2 probe is the only network call; budget is 200ms total.
package cloudenv

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"sync"
	"time"
)

type Kind string

const (
	KindLocal   Kind = "local"
	KindAWSEC2  Kind = "aws-ec2"
	KindUnknown Kind = "unknown"
)

// RuntimeInfo is the small bundle of facts the worker tells the control plane
// about where it lives. All fields except Kind are best-effort.
type RuntimeInfo struct {
	Kind             Kind   `json:"runtime_env"`
	InstanceID       string `json:"instance_id,omitempty"`
	Region           string `json:"region,omitempty"`
	AvailabilityZone string `json:"availability_zone,omitempty"`
}

const (
	defaultIMDSBase = "http://169.254.169.254"
	totalBudget     = 200 * time.Millisecond
	maxFieldLen     = 128
)

var (
	cacheOnce sync.Once
	cached    RuntimeInfo
)

// Detect returns the runtime info, cached after the first successful call.
func Detect() RuntimeInfo {
	cacheOnce.Do(func() { cached = detectFresh() })
	return cached
}

// detectFresh re-runs the full detection. Test-only; production callers use Detect.
func detectFresh() RuntimeInfo {
	info := RuntimeInfo{Kind: KindLocal}

	if v := os.Getenv("INFERIA_RUNTIME_ENV"); v != "" {
		info.Kind = Kind(truncate(v, maxFieldLen))
	}

	if info.Kind == KindLocal {
		if probed, ok := probeIMDS(); ok {
			info = probed
		}
	}

	// Per-field env overrides (apply on top of either env-Kind or IMDS).
	if v := os.Getenv("INFERIA_INSTANCE_ID"); v != "" {
		info.InstanceID = truncate(v, maxFieldLen)
	}
	if v := os.Getenv("INFERIA_REGION"); v != "" {
		info.Region = truncate(v, maxFieldLen)
	}
	if v := os.Getenv("INFERIA_AZ"); v != "" {
		info.AvailabilityZone = truncate(v, maxFieldLen)
	}
	return info
}

// probeIMDS runs IMDSv2 with a 200ms total budget. Returns (info, true) on
// success, (zero, false) on any failure (network, non-200, parse, etc).
func probeIMDS() (RuntimeInfo, bool) {
	base := os.Getenv("INFERIA_CLOUDENV_IMDS_URL")
	if base == "" {
		base = defaultIMDSBase
	}
	ctx, cancel := context.WithTimeout(context.Background(), totalBudget)
	defer cancel()

	// Step 1: PUT to get a session token.
	tokReq, err := http.NewRequestWithContext(ctx, http.MethodPut, base+"/latest/api/token", nil)
	if err != nil {
		return RuntimeInfo{}, false
	}
	tokReq.Header.Set("X-aws-ec2-metadata-token-ttl-seconds", "60")
	client := &http.Client{}
	tokResp, err := client.Do(tokReq)
	if err != nil {
		return RuntimeInfo{}, false
	}
	defer tokResp.Body.Close()
	if tokResp.StatusCode != http.StatusOK {
		return RuntimeInfo{}, false
	}
	tokBuf := make([]byte, 4096)
	n, _ := tokResp.Body.Read(tokBuf)
	token := string(tokBuf[:n])

	// Step 2: GET identity document.
	docReq, err := http.NewRequestWithContext(ctx, http.MethodGet, base+"/latest/dynamic/instance-identity/document", nil)
	if err != nil {
		return RuntimeInfo{}, false
	}
	docReq.Header.Set("X-aws-ec2-metadata-token", token)
	docResp, err := client.Do(docReq)
	if err != nil {
		return RuntimeInfo{}, false
	}
	defer docResp.Body.Close()
	if docResp.StatusCode != http.StatusOK {
		return RuntimeInfo{}, false
	}
	// Bound the body to defend against pathological responses.
	const maxBody = 64 * 1024
	docBuf := make([]byte, maxBody)
	bn, _ := docResp.Body.Read(docBuf)
	var doc struct {
		InstanceID       string `json:"instanceId"`
		Region           string `json:"region"`
		AvailabilityZone string `json:"availabilityZone"`
	}
	if err := json.Unmarshal(docBuf[:bn], &doc); err != nil {
		return RuntimeInfo{}, false
	}
	return RuntimeInfo{
		Kind:             KindAWSEC2,
		InstanceID:       truncate(doc.InstanceID, maxFieldLen),
		Region:           truncate(doc.Region, maxFieldLen),
		AvailabilityZone: truncate(doc.AvailabilityZone, maxFieldLen),
	}, true
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}
```

- [ ] **Step 4: Run test to verify both cases pass**

Run: `cd /storage/intern/hooman/work/inferia-worker && go test ./internal/cloudenv/... -v`
Expected: `PASS` for `TestDetect_EnvOverrideSetsKind` and `TestDetect_NoEnvNoIMDSReturnsLocal`.

- [ ] **Step 5: Commit (signed, no Claude mention)**

```bash
cd /storage/intern/hooman/work/inferia-worker
git add internal/cloudenv/
git commit -S -m "cloudenv: scaffold runtime-env detection with env overrides

New package internal/cloudenv exposes RuntimeInfo via a single Detect()
call. Env vars INFERIA_RUNTIME_ENV and INFERIA_{INSTANCE_ID,REGION,AZ}
override individual fields; IMDS path is in-place but tested only via
the unreachable-IMDS fallback for now. Real IMDSv2 probe coverage in
the next commit."
```

---

## Task 2: Add IMDSv2 probe coverage to `cloudenv.Detect` (Repo A)

**Files:**
- Modify: `inferia-worker/internal/cloudenv/detect_test.go`

- [ ] **Step 1: Add httptest-based IMDSv2 tests**

Append to `detect_test.go`:

```go
import (
	"net/http"
	"net/http/httptest"
)

func TestDetect_IMDSv2Success(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPut && r.URL.Path == "/latest/api/token":
			if r.Header.Get("X-aws-ec2-metadata-token-ttl-seconds") == "" {
				t.Errorf("missing TTL header")
			}
			w.Write([]byte("imds-token-abc"))
		case r.Method == http.MethodGet && r.URL.Path == "/latest/dynamic/instance-identity/document":
			if r.Header.Get("X-aws-ec2-metadata-token") != "imds-token-abc" {
				t.Errorf("missing/wrong token")
			}
			w.Write([]byte(`{"instanceId":"i-real","region":"eu-west-2","availabilityZone":"eu-west-2b"}`))
		default:
			http.Error(w, "unexpected", 400)
		}
	}))
	defer ts.Close()
	t.Setenv("INFERIA_CLOUDENV_IMDS_URL", ts.URL)
	t.Setenv("INFERIA_RUNTIME_ENV", "")

	got := detectFresh()
	if got.Kind != KindAWSEC2 || got.InstanceID != "i-real" || got.Region != "eu-west-2" || got.AvailabilityZone != "eu-west-2b" {
		t.Fatalf("got %+v", got)
	}
}

func TestDetect_IMDSv1Disabled(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Reject any GET without a token (IMDSv1 disabled), accept PUT.
		if r.Method == http.MethodPut {
			w.Write([]byte("tok"))
			return
		}
		if r.Header.Get("X-aws-ec2-metadata-token") == "tok" {
			w.Write([]byte(`{"instanceId":"i-v2","region":"us-west-2","availabilityZone":"us-west-2c"}`))
			return
		}
		http.Error(w, "v1 disabled", 401)
	}))
	defer ts.Close()
	t.Setenv("INFERIA_CLOUDENV_IMDS_URL", ts.URL)
	t.Setenv("INFERIA_RUNTIME_ENV", "")

	got := detectFresh()
	if got.Kind != KindAWSEC2 || got.InstanceID != "i-v2" {
		t.Fatalf("got %+v", got)
	}
}

func TestDetect_IMDSPayloadOversizeIsBounded(t *testing.T) {
	// Server returns a 1 MB JSON document. We should not OOM; we should fail
	// to parse and fall back to KindLocal.
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPut {
			w.Write([]byte("tok"))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		padding := make([]byte, 1024*1024)
		for i := range padding {
			padding[i] = 'x'
		}
		w.Write([]byte(`{"instanceId":"i","region":"r","availabilityZone":"a","pad":"`))
		w.Write(padding)
		w.Write([]byte(`"}`))
	}))
	defer ts.Close()
	t.Setenv("INFERIA_CLOUDENV_IMDS_URL", ts.URL)
	t.Setenv("INFERIA_RUNTIME_ENV", "")

	got := detectFresh()
	if got.Kind != KindLocal {
		t.Fatalf("oversize payload should fall back to local, got %+v", got)
	}
}

func TestDetect_Cached(t *testing.T) {
	hits := 0
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		if r.Method == http.MethodPut {
			w.Write([]byte("tok"))
			return
		}
		w.Write([]byte(`{"instanceId":"i","region":"r","availabilityZone":"a"}`))
	}))
	defer ts.Close()
	t.Setenv("INFERIA_CLOUDENV_IMDS_URL", ts.URL)
	t.Setenv("INFERIA_RUNTIME_ENV", "")

	// Reset the package-level cache for a clean test.
	cacheOnce = sync.Once{}
	_ = Detect()
	_ = Detect()
	_ = Detect()
	if hits > 2 {
		t.Fatalf("expected at most 2 IMDS hits (PUT + GET), got %d", hits)
	}
}
```

Also add `"sync"` to the imports list.

- [ ] **Step 2: Run the new tests**

Run: `cd /storage/intern/hooman/work/inferia-worker && go test ./internal/cloudenv/... -v`
Expected: all four new tests PASS. If `TestDetect_IMDSPayloadOversizeIsBounded` fails because the read isn't bounded, fix `probeIMDS` so the read uses `io.LimitReader(docResp.Body, maxBody)` rather than a single Read into a 64KB buffer.

- [ ] **Step 3: If the oversize test fails, tighten the read**

In `detect.go`, replace the `docBuf := make([]byte, maxBody); bn, _ := docResp.Body.Read(docBuf)` block with:

```go
import "io"
// ...
limited := io.LimitReader(docResp.Body, maxBody)
docBytes, err := io.ReadAll(limited)
if err != nil {
	return RuntimeInfo{}, false
}
// If we filled exactly maxBody, body was truncated; treat as malformed.
if len(docBytes) >= maxBody {
	return RuntimeInfo{}, false
}
var doc struct { /* same as before */ }
if err := json.Unmarshal(docBytes, &doc); err != nil {
	return RuntimeInfo{}, false
}
```

Re-run tests; expect all four to PASS.

- [ ] **Step 4: Commit**

```bash
cd /storage/intern/hooman/work/inferia-worker
git add internal/cloudenv/
git commit -S -m "cloudenv: IMDSv2 probe with httptest coverage

Adds tests for the IMDSv2 success path, IMDSv1-disabled hosts, oversized
identity-document defense (capped at 64KB), and the once-only cache.
Tightens the body read to io.LimitReader so a hostile IMDS can't OOM us."
```

---

## Task 3: Extend register POST body with cloud-env fields (Repo A)

**Files:**
- Modify: `inferia-worker/internal/control/bootstrap.go`
- Modify: `inferia-worker/internal/control/bootstrap_test.go`

- [ ] **Step 1: Read existing bootstrap.go to confirm current shape**

Run: `cd /storage/intern/hooman/work/inferia-worker && wc -l internal/control/bootstrap.go && head -60 internal/control/bootstrap.go`
Note the existing struct name for the request body (likely `RegisterRequest`) and the function name that builds it.

- [ ] **Step 2: Add a failing test**

Append to `bootstrap_test.go`:

```go
func TestRegisterRequest_IncludesCloudEnv(t *testing.T) {
	info := cloudenv.RuntimeInfo{
		Kind:             cloudenv.KindAWSEC2,
		InstanceID:       "i-abc",
		Region:           "us-east-1",
		AvailabilityZone: "us-east-1a",
	}
	req := BuildRegisterRequest(BuildRegisterInput{
		NodeName:    "node-1",
		PoolID:      "pool-x",
		Allocatable: map[string]string{"cpu": "8", "gpu": "1"},
		Runtime:     info,
	})
	data, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	s := string(data)
	for _, want := range []string{
		`"runtime_env":"aws-ec2"`,
		`"instance_id":"i-abc"`,
		`"region":"us-east-1"`,
		`"availability_zone":"us-east-1a"`,
	} {
		if !strings.Contains(s, want) {
			t.Errorf("missing %q in %s", want, s)
		}
	}
}

func TestRegisterRequest_OmitsCloudEnvWhenLocal(t *testing.T) {
	info := cloudenv.RuntimeInfo{Kind: cloudenv.KindLocal}
	req := BuildRegisterRequest(BuildRegisterInput{
		NodeName:    "node-1",
		PoolID:      "pool-x",
		Allocatable: map[string]string{"cpu": "8"},
		Runtime:     info,
	})
	data, _ := json.Marshal(req)
	s := string(data)
	if strings.Contains(s, "instance_id") || strings.Contains(s, "region") || strings.Contains(s, "availability_zone") {
		t.Errorf("local runtime should omit cloud-env fields: %s", s)
	}
	// runtime_env "local" is fine to include OR omit; either is acceptable —
	// pin to "include" so the CP always has the field.
	if !strings.Contains(s, `"runtime_env":"local"`) {
		t.Errorf("runtime_env=local should be present: %s", s)
	}
}
```

Imports needed at top of test file: `"encoding/json"`, `"strings"`, `"github.com/inferia/inferia-worker/internal/cloudenv"`.

- [ ] **Step 3: Run the new tests; observe failure**

Run: `cd /storage/intern/hooman/work/inferia-worker && go test ./internal/control/... -run TestRegisterRequest -v`
Expected: undefined `BuildRegisterRequest`, `BuildRegisterInput`, or compile failures.

- [ ] **Step 4: Update `bootstrap.go` to accept the runtime info**

Find the existing register-request builder in `bootstrap.go`. Add a `Runtime cloudenv.RuntimeInfo` field to its input struct and propagate it into the body. If the file uses ad-hoc `map[string]any`, refactor to a typed `RegisterRequest` struct with `omitempty` on the cloud-env fields:

```go
import "github.com/inferia/inferia-worker/internal/cloudenv"

type BuildRegisterInput struct {
	NodeName     string
	PoolID       string
	Allocatable  map[string]string
	AdvertiseURL string
	Runtime      cloudenv.RuntimeInfo
}

type RegisterRequest struct {
	NodeName         string            `json:"node_name"`
	PoolID           string            `json:"pool_id"`
	Allocatable      map[string]string `json:"allocatable"`
	AdvertiseURL     string            `json:"advertise_url,omitempty"`
	RuntimeEnv       string            `json:"runtime_env,omitempty"`
	InstanceID       string            `json:"instance_id,omitempty"`
	Region           string            `json:"region,omitempty"`
	AvailabilityZone string            `json:"availability_zone,omitempty"`
	BootstrapToken   string            `json:"bootstrap_token,omitempty"`
}

func BuildRegisterRequest(in BuildRegisterInput) RegisterRequest {
	return RegisterRequest{
		NodeName:         in.NodeName,
		PoolID:           in.PoolID,
		Allocatable:      in.Allocatable,
		AdvertiseURL:     in.AdvertiseURL,
		RuntimeEnv:       string(in.Runtime.Kind),
		InstanceID:       in.Runtime.InstanceID,
		Region:           in.Runtime.Region,
		AvailabilityZone: in.Runtime.AvailabilityZone,
	}
}
```

Update the call sites in the same file (existing register function) to call `BuildRegisterRequest`. Keep the existing public function signature stable; add the `Runtime` only as a new parameter where it's called from `cmd/worker/main.go` (Task 5 wires it).

- [ ] **Step 5: Run all `internal/control` tests; all should pass**

Run: `cd /storage/intern/hooman/work/inferia-worker && go test ./internal/control/... -v`
Expected: all green, including the two new tests.

- [ ] **Step 6: Commit**

```bash
cd /storage/intern/hooman/work/inferia-worker
git add internal/control/
git commit -S -m "control: include cloud-env fields in register request body

RegisterRequest gains runtime_env, instance_id, region, availability_zone
(all omitempty). The values come from internal/cloudenv.RuntimeInfo,
threaded in by the caller. Off-cloud workers send only runtime_env=local;
on-AWS workers send all four."
```

---

## Task 4: Extend `HelloBody` with cloud-env fields (Repo A)

**Files:**
- Modify: `inferia-worker/internal/control/protocol.go`
- Modify: `inferia-worker/internal/control/channel.go`
- Modify: `inferia-worker/internal/control/channel_test.go`

- [ ] **Step 1: Add failing Hello-frame test**

Append to `channel_test.go`:

```go
func TestHello_IncludesCloudEnv(t *testing.T) {
	frames := captureWrittenFrames(t, func(ch *Channel) {
		ch.Runtime = cloudenv.RuntimeInfo{
			Kind:             cloudenv.KindAWSEC2,
			InstanceID:       "i-hello",
			Region:           "us-east-1",
			AvailabilityZone: "us-east-1c",
		}
		ch.sendHello(context.Background(), nil /* the test helper supplies the conn */)
	})
	if len(frames) == 0 {
		t.Fatal("no frames captured")
	}
	var env Envelope
	if err := json.Unmarshal(frames[0], &env); err != nil {
		t.Fatal(err)
	}
	if env.Type != MsgHello {
		t.Fatalf("first frame is %q, want Hello", env.Type)
	}
	body, _ := json.Marshal(env.Body)
	for _, want := range []string{`"runtime_env":"aws-ec2"`, `"instance_id":"i-hello"`, `"region":"us-east-1"`, `"availability_zone":"us-east-1c"`} {
		if !strings.Contains(string(body), want) {
			t.Errorf("Hello body missing %q: %s", want, body)
		}
	}
}
```

`captureWrittenFrames` is a test helper — if it doesn't already exist, add a small one at the top of `channel_test.go`:

```go
// captureWrittenFrames returns the JSON frames written via the conn during fn.
// Uses an in-memory websocket via net.Pipe.
func captureWrittenFrames(t *testing.T, fn func(*Channel)) [][]byte {
	t.Helper()
	server, client := net.Pipe()
	defer server.Close()
	defer client.Close()
	// Channel writes to `client`-side of the pipe; reads happen on `server`.
	ch := &Channel{}
	// Set up minimal channel state. The fn drives any send.
	frames := [][]byte{}
	done := make(chan struct{})
	go func() {
		defer close(done)
		buf := make([]byte, 64*1024)
		for {
			n, err := server.Read(buf)
			if n > 0 {
				frames = append(frames, append([]byte(nil), buf[:n]...))
			}
			if err != nil {
				return
			}
		}
	}()
	fn(ch)
	client.Close()
	<-done
	return frames
}
```

Adapt as needed to match the actual websocket abstraction in `channel.go`.

- [ ] **Step 2: Run test; observe failure**

Run: `cd /storage/intern/hooman/work/inferia-worker && go test ./internal/control/... -run TestHello -v`
Expected: undefined `ch.Runtime`, undefined `sendHello`, or `HelloBody` doesn't carry the fields.

- [ ] **Step 3: Extend `HelloBody` and `Channel`**

In `protocol.go`, extend `HelloBody`:

```go
type HelloBody struct {
	NodeName         string            `json:"node_name,omitempty"`
	Capabilities     map[string]string `json:"capabilities,omitempty"`
	LoadedDeployments []string         `json:"loaded_deployments,omitempty"`
	RuntimeEnv       string            `json:"runtime_env,omitempty"`
	InstanceID       string            `json:"instance_id,omitempty"`
	Region           string            `json:"region,omitempty"`
	AvailabilityZone string            `json:"availability_zone,omitempty"`
}
```

(Preserve the existing fields — only ADD the four cloud-env ones.)

In `channel.go`, add a `Runtime cloudenv.RuntimeInfo` field on `Channel` and have the existing Hello-sending code (search for `MsgHello` usage and where `HelloBody` is constructed) include those fields. If there's no `sendHello` helper today, extract one — but keep it tiny.

- [ ] **Step 4: Run; all tests green**

Run: `cd /storage/intern/hooman/work/inferia-worker && go test ./internal/control/... -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
cd /storage/intern/hooman/work/inferia-worker
git add internal/control/
git commit -S -m "control: Hello frame carries cloud-env fields

HelloBody gets runtime_env, instance_id, region, availability_zone
(omitempty). Channel.Runtime is a new field set by main.go; sendHello
threads it into the outbound frame so the CP refreshes inventory
labels on every reconnect, not just on first register."
```

---

## Task 5: Wire `cloudenv.Detect()` in `cmd/worker/main.go` (Repo A)

**Files:**
- Modify: `inferia-worker/cmd/worker/main.go`

- [ ] **Step 1: Read main.go to locate the bootstrap call site and channel constructor**

Run: `cd /storage/intern/hooman/work/inferia-worker && grep -n "bootstrap\|Channel{" cmd/worker/main.go`

- [ ] **Step 2: Wire Detect() once**

Near the top of `runWorker` (or wherever the bootstrap/channel are set up), add:

```go
import "github.com/inferia/inferia-worker/internal/cloudenv"

// ...

runtimeInfo := cloudenv.Detect()
log.Printf("cloudenv: kind=%s instance=%s region=%s az=%s",
	runtimeInfo.Kind, runtimeInfo.InstanceID, runtimeInfo.Region, runtimeInfo.AvailabilityZone)
```

Pass `runtimeInfo` into:
- the `BuildRegisterInput` used in the bootstrap (Task 3 added the `Runtime` field).
- the `Channel` constructor — set `ch.Runtime = runtimeInfo` after the struct literal.

- [ ] **Step 3: Build to confirm wiring**

Run: `cd /storage/intern/hooman/work/inferia-worker && go build ./...`
Expected: build succeeds.

- [ ] **Step 4: Run full test suite for the worker repo**

Run: `cd /storage/intern/hooman/work/inferia-worker && go test ./... -race -count=1`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
cd /storage/intern/hooman/work/inferia-worker
git add cmd/worker/main.go
git commit -S -m "main: wire cloudenv.Detect() into bootstrap and Hello

Single Detect() call at startup; result threaded into the register
request and into Channel so reconnects also carry the fields.
On non-AWS hosts the IMDS probe times out in 200ms and the worker
proceeds as runtime_env=local with no extra fields on the wire."
```

---

## Task 6: Multi-arch Dockerfile (Repo A)

**Files:**
- Modify: `inferia-worker/Dockerfile`

- [ ] **Step 1: Change hard-coded GOARCH**

Replace line 16 in `Dockerfile`:

```dockerfile
# before:
ENV CGO_ENABLED=0 GOOS=linux GOARCH=amd64
# after:
ARG TARGETOS=linux
ARG TARGETARCH=amd64
ENV CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH}
```

- [ ] **Step 2: Local sanity build (single arch)**

Run: `cd /storage/intern/hooman/work/inferia-worker && docker build -t inferia-worker:plan-task6 .`
Expected: success, image tag exists.

- [ ] **Step 3: Multi-arch dry-run (no push)**

Run: `cd /storage/intern/hooman/work/inferia-worker && docker buildx build --platform linux/amd64,linux/arm64 --output=type=cacheonly .`
Expected: both arches build successfully. (If buildx isn't installed, skip and rely on CI to exercise this; note the limitation in the commit message.)

- [ ] **Step 4: Commit**

```bash
cd /storage/intern/hooman/work/inferia-worker
git add Dockerfile
git commit -S -m "docker: parameterize GOARCH via TARGETARCH for multi-arch buildx

amd64 was hard-coded, blocking the linux/arm64 lane in CI. Switching
to the standard TARGETOS/TARGETARCH build args lets docker buildx
produce both architectures from one Dockerfile."
```

---

## Task 7: GitHub Action — test workflow (Repo A)

**Files:**
- Create: `inferia-worker/.github/workflows/test.yml`

- [ ] **Step 1: Write the workflow**

```yaml
# inferia-worker/.github/workflows/test.yml
name: Test

on:
  pull_request:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-go@v5
        with:
          go-version: "1.26"
          cache: true

      - name: go vet
        run: go vet ./...

      - name: go test (race + cover)
        run: |
          go test ./... -race -count=1 -coverprofile=coverage.out
          go tool cover -func=coverage.out | tail -5

      - name: go build
        run: go build ./...

      - name: Multi-arch Dockerfile dry-run
        run: |
          docker buildx create --use --name multiarch || true
          docker buildx build --platform linux/amd64,linux/arm64 --output=type=cacheonly .
```

- [ ] **Step 2: Validate workflow syntax locally if `act` or similar is available; otherwise rely on first PR run**

If you have `act`: `act --container-architecture linux/amd64 -j test --dry-run`. Otherwise skip — first push will exercise it.

- [ ] **Step 3: Commit**

```bash
cd /storage/intern/hooman/work/inferia-worker
git add .github/workflows/test.yml
git commit -S -m "ci: add go vet/test/build workflow with multi-arch Dockerfile dry-run

Runs on PRs and main pushes. The buildx dry-run catches Dockerfile
breakage on arm64 before the tag-publish lane runs for real."
```

---

## Task 8: GitHub Action — Docker publish workflow (Repo A)

**Files:**
- Create: `inferia-worker/.github/workflows/docker-publish.yml`

- [ ] **Step 1: Write the workflow**

```yaml
# inferia-worker/.github/workflows/docker-publish.yml
name: Build and Publish Docker Image

on:
  push:
    tags: ["v*"]
  workflow_dispatch:

permissions:
  contents: read
  packages: write

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository_owner }}/inferia-worker

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=raw,value=latest,enable=${{ github.ref_type == 'tag' }}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
```

- [ ] **Step 2: README note (≤20 lines added)**

Add a short section to `inferia-worker/README.md`:

```markdown
## Docker image

The worker is published to GHCR on every `v*` tag:

```
docker pull ghcr.io/<org>/inferia-worker:latest
docker pull ghcr.io/<org>/inferia-worker:v1.2.3
```

`<org>` is the repository owner. Images are multi-arch (linux/amd64, linux/arm64).
On a fresh EC2 instance, cloud-init handles `docker run` automatically when the
node is provisioned via InferiaLLM's AWS adapter.
```

- [ ] **Step 3: Commit**

```bash
cd /storage/intern/hooman/work/inferia-worker
git add .github/workflows/docker-publish.yml README.md
git commit -S -m "ci: publish multi-arch GHCR image on v* tags

Uses GITHUB_TOKEN (no PAT required for owner-owned packages) to push
ghcr.io/<owner>/inferia-worker for linux/amd64 + linux/arm64. Triggered
by tag push and workflow_dispatch. README adds a short pull-instructions
section."
```

---

## Task 9: DB migration `worker_bootstrap_tokens` (Repo B)

**Files:**
- Create: `InferiaLLM/package/src/inferia/infra/schema/migrations/20260520_add_worker_bootstrap_tokens.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 20260520_add_worker_bootstrap_tokens.sql
-- One-shot tokens minted by the orchestration service when provisioning a
-- new node (initially AWS EC2, eventually any cloud adapter). The token is
-- embedded in cloud-init user-data; the worker presents it once to
-- /v1/workers/register, the row is atomically marked consumed, and the
-- worker receives a long-lived WorkerJWT in exchange.

CREATE TABLE IF NOT EXISTS worker_bootstrap_tokens (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash        text NOT NULL UNIQUE,
    pool_id           uuid NOT NULL REFERENCES compute_pool(id) ON DELETE CASCADE,
    org_id            uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    expires_at        timestamptz NOT NULL,
    consumed_at       timestamptz NULL,
    consumed_node_id  uuid NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_worker_bootstrap_tokens_pool
    ON worker_bootstrap_tokens(pool_id);

CREATE INDEX IF NOT EXISTS idx_worker_bootstrap_tokens_unconsumed
    ON worker_bootstrap_tokens(expires_at)
    WHERE consumed_at IS NULL;
```

- [ ] **Step 2: Run the migration against a local dev DB to verify it applies cleanly**

Run: `cd /storage/intern/hooman/work/InferiaLLM && inferiallm init` (or whatever invokes the migration runner — check `package/src/inferia/cli.py` for the exact command if unsure).
Expected: migration applies without error. Inspect with `psql $DATABASE_URL -c "\d worker_bootstrap_tokens"`.

- [ ] **Step 3: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add package/src/inferia/infra/schema/migrations/20260520_add_worker_bootstrap_tokens.sql
git commit -S -m "schema: add worker_bootstrap_tokens table

One-shot tokens for cloud-provisioned workers. Hash storage only,
pool+org scoped, atomic consume via UPDATE ... WHERE consumed_at
IS NULL. Partial index keeps unconsumed-token lookups cheap as the
table grows."
```

---

## Task 10: `mint_bootstrap_token` + `consume_bootstrap_token` in `auth.py` (Repo B)

**Files:**
- Modify: `InferiaLLM/package/src/inferia/services/orchestration/services/worker_controller/auth.py`
- Modify: `InferiaLLM/package/src/inferia/services/orchestration/services/worker_controller/test_auth.py`

- [ ] **Step 1: Read existing `auth.py` to confirm conventions (sync vs async, DB session type)**

Run: `cd /storage/intern/hooman/work/InferiaLLM && head -40 package/src/inferia/services/orchestration/services/worker_controller/auth.py`

- [ ] **Step 2: Add failing tests**

Append to `test_auth.py`:

```python
import asyncio
import hashlib

import pytest

from inferia.services.orchestration.services.worker_controller.auth import (
    InvalidBootstrapToken,
    consume_bootstrap_token,
    mint_bootstrap_token,
)


@pytest.mark.asyncio
async def test_mint_returns_distinct_tokens(db_conn, pool_row, org_row):
    t1, b1 = await mint_bootstrap_token(db_conn, pool_id=pool_row["id"], org_id=org_row["id"])
    t2, b2 = await mint_bootstrap_token(db_conn, pool_id=pool_row["id"], org_id=org_row["id"])
    assert t1 != t2
    assert b1 != b2
    assert len(t1) >= 32  # URL-safe 32-byte token


@pytest.mark.asyncio
async def test_mint_stores_hash_not_plaintext(db_conn, pool_row, org_row):
    token, _ = await mint_bootstrap_token(db_conn, pool_id=pool_row["id"], org_id=org_row["id"])
    row = await db_conn.fetchrow(
        "SELECT token_hash FROM worker_bootstrap_tokens WHERE token_hash = $1",
        hashlib.sha256(token.encode()).hexdigest(),
    )
    assert row is not None
    bad = await db_conn.fetchrow(
        "SELECT 1 FROM worker_bootstrap_tokens WHERE token_hash = $1", token
    )
    assert bad is None, "plaintext token must not appear in token_hash column"


@pytest.mark.asyncio
async def test_consume_happy(db_conn, pool_row, org_row):
    token, bid = await mint_bootstrap_token(db_conn, pool_id=pool_row["id"], org_id=org_row["id"])
    claim = await consume_bootstrap_token(db_conn, token=token)
    assert claim.bootstrap_id == bid
    assert claim.pool_id == pool_row["id"]
    assert claim.org_id == org_row["id"]


@pytest.mark.asyncio
async def test_consume_double_use_rejected(db_conn, pool_row, org_row):
    token, _ = await mint_bootstrap_token(db_conn, pool_id=pool_row["id"], org_id=org_row["id"])
    await consume_bootstrap_token(db_conn, token=token)
    with pytest.raises(InvalidBootstrapToken):
        await consume_bootstrap_token(db_conn, token=token)


@pytest.mark.asyncio
async def test_consume_unknown_rejected(db_conn):
    with pytest.raises(InvalidBootstrapToken):
        await consume_bootstrap_token(db_conn, token="not-a-real-token")


@pytest.mark.asyncio
async def test_consume_expired_rejected(db_conn, pool_row, org_row):
    token, _ = await mint_bootstrap_token(
        db_conn, pool_id=pool_row["id"], org_id=org_row["id"], ttl_seconds=-1
    )
    with pytest.raises(InvalidBootstrapToken):
        await consume_bootstrap_token(db_conn, token=token)


@pytest.mark.asyncio
async def test_consume_race_only_one_wins(db_conn_pool, pool_row, org_row):
    """Two simultaneous consumes of the same token: exactly one succeeds."""
    async with db_conn_pool.acquire() as conn:
        token, _ = await mint_bootstrap_token(conn, pool_id=pool_row["id"], org_id=org_row["id"])

    async def consume_in_own_conn():
        async with db_conn_pool.acquire() as c:
            try:
                return await consume_bootstrap_token(c, token=token)
            except InvalidBootstrapToken:
                return None

    results = await asyncio.gather(consume_in_own_conn(), consume_in_own_conn())
    successes = [r for r in results if r is not None]
    assert len(successes) == 1, f"expected exactly one winner, got {successes}"
```

If `db_conn`, `pool_row`, `org_row`, or `db_conn_pool` fixtures don't exist in `conftest.py` already, add them — check existing `test_auth.py` for the pattern. (The repo's conftest typically supplies these; if not, follow the pattern in `test_registry.py`.)

- [ ] **Step 3: Run tests; observe failure**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/services/orchestration/services/worker_controller/test_auth.py::test_mint_returns_distinct_tokens -v`
Expected: ImportError on `mint_bootstrap_token` / `consume_bootstrap_token` / `InvalidBootstrapToken`.

- [ ] **Step 4: Implement in `auth.py`**

Append (don't replace) to `auth.py`:

```python
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import asyncpg


class InvalidBootstrapToken(Exception):
    """Raised when a bootstrap token is unknown, already consumed, or expired."""


@dataclass(frozen=True)
class BootstrapClaim:
    bootstrap_id: UUID
    pool_id: UUID
    org_id: UUID


DEFAULT_BOOTSTRAP_TTL_SECONDS = 3600


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def mint_bootstrap_token(
    conn: asyncpg.Connection,
    *,
    pool_id: UUID,
    org_id: UUID,
    ttl_seconds: int = DEFAULT_BOOTSTRAP_TTL_SECONDS,
) -> tuple[str, UUID]:
    """Generate a fresh URL-safe token, store its SHA-256 hash, return
    (plaintext_token, bootstrap_id). Negative TTL is allowed for tests:
    it produces a row whose expires_at is already in the past."""
    token = secrets.token_urlsafe(32)
    bid = uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    await conn.execute(
        """
        INSERT INTO worker_bootstrap_tokens (id, token_hash, pool_id, org_id, expires_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        bid,
        _hash(token),
        pool_id,
        org_id,
        expires_at,
    )
    return token, bid


async def consume_bootstrap_token(
    conn: asyncpg.Connection,
    *,
    token: str,
) -> BootstrapClaim:
    """Atomic consume: returns BootstrapClaim or raises InvalidBootstrapToken."""
    row = await conn.fetchrow(
        """
        UPDATE worker_bootstrap_tokens
        SET consumed_at = now()
        WHERE token_hash = $1
          AND consumed_at IS NULL
          AND expires_at > now()
        RETURNING id, pool_id, org_id
        """,
        _hash(token),
    )
    if row is None:
        raise InvalidBootstrapToken()
    return BootstrapClaim(
        bootstrap_id=row["id"],
        pool_id=row["pool_id"],
        org_id=row["org_id"],
    )
```

- [ ] **Step 5: Run tests; expect green**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/services/orchestration/services/worker_controller/test_auth.py -v -k bootstrap`
Expected: all 7 new tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add package/src/inferia/services/orchestration/services/worker_controller/auth.py \
        package/src/inferia/services/orchestration/services/worker_controller/test_auth.py
git commit -S -m "auth: mint and atomically consume bootstrap tokens

mint_bootstrap_token stores SHA-256(token) only; the plaintext is
returned to the caller exactly once for embedding in user-data.
consume_bootstrap_token uses an UPDATE ... WHERE consumed_at IS NULL
to guarantee single-use under concurrent registers. Tests cover the
race, expiry, double-use, and hash-not-plaintext storage."
```

---

## Task 11: Extend `/v1/workers/register` to accept bootstrap_token + cloud-env fields (Repo B)

**Files:**
- Modify: `InferiaLLM/package/src/inferia/services/orchestration/api/workers.py`
- Modify: `InferiaLLM/package/src/inferia/services/orchestration/api/test_workers.py`

- [ ] **Step 1: Read existing register handler to confirm shape**

Run: `cd /storage/intern/hooman/work/InferiaLLM && grep -n "register\|RegisterRequest\|/register" package/src/inferia/services/orchestration/api/workers.py | head -20`

- [ ] **Step 2: Add failing tests**

Append to `test_workers.py`:

```python
@pytest.mark.asyncio
async def test_register_with_bootstrap_token_happy(client, db_conn, pool_row, org_row):
    from inferia.services.orchestration.services.worker_controller.auth import (
        mint_bootstrap_token,
    )
    token, _ = await mint_bootstrap_token(
        db_conn, pool_id=pool_row["id"], org_id=org_row["id"]
    )
    resp = await client.post(
        "/v1/workers/register",
        json={
            "bootstrap_token": token,
            "node_name": "i-abc",
            "pool_id": str(pool_row["id"]),
            "allocatable": {"cpu": "8", "gpu": "1"},
            "runtime_env": "aws-ec2",
            "instance_id": "i-abc",
            "region": "us-east-1",
            "availability_zone": "us-east-1a",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "node_id" in body
    assert "worker_jwt" in body

    # Token is consumed; second register attempt with the same token fails.
    resp2 = await client.post(
        "/v1/workers/register",
        json={
            "bootstrap_token": token,
            "node_name": "i-abc",
            "pool_id": str(pool_row["id"]),
            "allocatable": {"cpu": "8"},
        },
    )
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_register_records_cloud_env_in_labels(client, db_conn, pool_row, org_row):
    from inferia.services.orchestration.services.worker_controller.auth import (
        mint_bootstrap_token,
    )
    token, _ = await mint_bootstrap_token(
        db_conn, pool_id=pool_row["id"], org_id=org_row["id"]
    )
    resp = await client.post(
        "/v1/workers/register",
        json={
            "bootstrap_token": token,
            "node_name": "i-xyz",
            "pool_id": str(pool_row["id"]),
            "allocatable": {"cpu": "8"},
            "runtime_env": "aws-ec2",
            "instance_id": "i-xyz",
            "region": "eu-west-1",
            "availability_zone": "eu-west-1c",
        },
    )
    assert resp.status_code == 200, resp.text
    node_id = resp.json()["node_id"]
    row = await db_conn.fetchrow(
        "SELECT labels FROM compute_inventory WHERE node_id = $1", node_id
    )
    labels = row["labels"]
    assert labels["runtime_env"] == "aws-ec2"
    assert labels["instance_id"] == "i-xyz"
    assert labels["region"] == "eu-west-1"
    assert labels["availability_zone"] == "eu-west-1c"


@pytest.mark.asyncio
async def test_register_without_cloud_env_still_works(client, db_conn, pool_row, org_row):
    from inferia.services.orchestration.services.worker_controller.auth import (
        mint_bootstrap_token,
    )
    token, _ = await mint_bootstrap_token(
        db_conn, pool_id=pool_row["id"], org_id=org_row["id"]
    )
    resp = await client.post(
        "/v1/workers/register",
        json={
            "bootstrap_token": token,
            "node_name": "local-1",
            "pool_id": str(pool_row["id"]),
            "allocatable": {"cpu": "4"},
        },
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_register_bootstrap_token_pool_mismatch_rejected(client, db_conn, pool_row, other_pool_row, org_row):
    """Token minted for pool A; register claims pool B → 401."""
    from inferia.services.orchestration.services.worker_controller.auth import (
        mint_bootstrap_token,
    )
    token, _ = await mint_bootstrap_token(
        db_conn, pool_id=pool_row["id"], org_id=org_row["id"]
    )
    resp = await client.post(
        "/v1/workers/register",
        json={
            "bootstrap_token": token,
            "node_name": "wrong-pool",
            "pool_id": str(other_pool_row["id"]),
            "allocatable": {"cpu": "4"},
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_register_oversized_fields_rejected(client, db_conn, pool_row, org_row):
    from inferia.services.orchestration.services.worker_controller.auth import (
        mint_bootstrap_token,
    )
    token, _ = await mint_bootstrap_token(
        db_conn, pool_id=pool_row["id"], org_id=org_row["id"]
    )
    resp = await client.post(
        "/v1/workers/register",
        json={
            "bootstrap_token": token,
            "node_name": "n",
            "pool_id": str(pool_row["id"]),
            "allocatable": {"cpu": "1"},
            "runtime_env": "x" * 200,  # > 64 chars
        },
    )
    assert resp.status_code == 422  # FastAPI validation error
```

Add an `other_pool_row` fixture in `conftest.py` if not present — copy the existing `pool_row` fixture and insert a second pool.

- [ ] **Step 3: Run tests; observe failure**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/services/orchestration/api/test_workers.py -v -k "bootstrap or cloud_env or pool_mismatch or oversized"`
Expected: failures on the new tests because the endpoint doesn't accept the new fields yet.

- [ ] **Step 4: Extend the Pydantic request model and handler**

In `workers.py`, locate the existing `RegisterRequest` model and the `/v1/workers/register` POST handler.

Update the model:

```python
from pydantic import BaseModel, Field
from typing import Optional

class RegisterRequest(BaseModel):
    # existing fields stay; add:
    bootstrap_token: Optional[str] = Field(default=None, min_length=10, max_length=128)
    runtime_env: Optional[str] = Field(default=None, max_length=64)
    instance_id: Optional[str] = Field(default=None, max_length=128)
    region: Optional[str] = Field(default=None, max_length=64)
    availability_zone: Optional[str] = Field(default=None, max_length=64)
```

Update the handler to consume the bootstrap token + record cloud-env. Pseudocode skeleton (adapt to the existing handler structure):

```python
from inferia.services.orchestration.services.worker_controller.auth import (
    consume_bootstrap_token,
    InvalidBootstrapToken,
)

@router.post("/v1/workers/register")
async def register(req: RegisterRequest, db = Depends(get_db_conn)):
    async with db.transaction():
        if req.bootstrap_token is not None:
            try:
                claim = await consume_bootstrap_token(db, token=req.bootstrap_token)
            except InvalidBootstrapToken:
                raise HTTPException(status_code=401, detail="invalid_bootstrap_token")
            if str(claim.pool_id) != req.pool_id:
                raise HTTPException(status_code=401, detail="pool_scope_violation")
        # else: existing legacy worker-JWT path stays unchanged.

        labels = {}
        if req.runtime_env:
            labels["runtime_env"] = req.runtime_env
        if req.instance_id:
            labels["instance_id"] = req.instance_id
        if req.region:
            labels["region"] = req.region
        if req.availability_zone:
            labels["availability_zone"] = req.availability_zone

        node_id = await upsert_compute_inventory(  # existing helper
            db,
            pool_id=req.pool_id,
            node_name=req.node_name,
            allocatable=req.allocatable,
            labels=labels,  # merged with existing labels jsonb
            state="ready",
        )
        worker_jwt = mint_worker_jwt(node_id=node_id, pool_id=req.pool_id)  # existing helper

    return {"node_id": str(node_id), "worker_jwt": worker_jwt}
```

The transaction wrapper ensures token consumption and inventory upsert succeed or fail together — no orphaned consumed tokens.

- [ ] **Step 5: Run tests; expect green**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/services/orchestration/api/test_workers.py -v`
Expected: all green, including legacy register tests.

- [ ] **Step 6: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add package/src/inferia/services/orchestration/api/workers.py \
        package/src/inferia/services/orchestration/api/test_workers.py
git commit -S -m "workers: register accepts bootstrap_token and cloud-env metadata

POST /v1/workers/register now accepts an optional bootstrap_token. If
present it's atomically consumed in the same transaction as the
compute_inventory upsert; pool-scope mismatch returns 401. Optional
runtime_env / instance_id / region / availability_zone fields are
length-bounded by the Pydantic model and merged into the inventory
row's labels jsonb. Legacy worker-JWT registers are unchanged."
```

---

## Task 12: `common/runtime_env.py` helper (Repo B)

**Files:**
- Create: `InferiaLLM/package/src/inferia/common/runtime_env.py`
- Create: `InferiaLLM/package/src/inferia/common/tests/test_runtime_env.py`

- [ ] **Step 1: Write failing tests**

```python
# package/src/inferia/common/tests/test_runtime_env.py
import os
from unittest.mock import patch

import httpx
import pytest

from inferia.common import runtime_env


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("INFERIA_RUNTIME_ENV", "aws-ec2")
    # Reset cache.
    runtime_env._CACHE.clear()
    assert runtime_env.detect_runtime_env() == "aws-ec2"


def test_no_env_no_imds_returns_local(monkeypatch):
    monkeypatch.delenv("INFERIA_RUNTIME_ENV", raising=False)
    monkeypatch.setenv("INFERIA_CLOUDENV_IMDS_URL", "http://127.0.0.1:1")
    runtime_env._CACHE.clear()
    assert runtime_env.detect_runtime_env() == "local"


def test_imds_success(monkeypatch, httpx_mock):
    monkeypatch.delenv("INFERIA_RUNTIME_ENV", raising=False)
    monkeypatch.setenv("INFERIA_CLOUDENV_IMDS_URL", "http://imds")
    httpx_mock.add_response(method="PUT", url="http://imds/latest/api/token", text="tok")
    httpx_mock.add_response(
        method="GET",
        url="http://imds/latest/dynamic/instance-identity/document",
        json={"instanceId": "i-1", "region": "us-east-1", "availabilityZone": "us-east-1a"},
    )
    runtime_env._CACHE.clear()
    assert runtime_env.detect_runtime_env() == "aws-ec2"


def test_cached(monkeypatch, httpx_mock):
    monkeypatch.setenv("INFERIA_RUNTIME_ENV", "aws-ec2")
    runtime_env._CACHE.clear()
    runtime_env.detect_runtime_env()
    runtime_env.detect_runtime_env()
    runtime_env.detect_runtime_env()
    # Cache hit: only the first call should do any work.
    assert runtime_env._CACHE["env"] == "aws-ec2"
```

- [ ] **Step 2: Run tests; observe failure**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/common/tests/test_runtime_env.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# package/src/inferia/common/runtime_env.py
"""CP-side detection of the runtime environment InferiaLLM itself runs in.

Used for telemetry and logging only — load-bearing detection lives on the
worker side (inferia-worker/internal/cloudenv).

Cached for the process lifetime; reset via ``_CACHE.clear()`` in tests.
"""
from __future__ import annotations

import os
from typing import Literal

import httpx

RuntimeEnv = Literal["local", "aws-ec2", "k8s", "unknown"]

_CACHE: dict[str, RuntimeEnv] = {}
_IMDS_TIMEOUT_S = 0.2


def detect_runtime_env() -> RuntimeEnv:
    cached = _CACHE.get("env")
    if cached is not None:
        return cached
    env = _detect()
    _CACHE["env"] = env
    return env


def _detect() -> RuntimeEnv:
    v = os.getenv("INFERIA_RUNTIME_ENV")
    if v:
        return v[:64]  # type: ignore[return-value]
    base = os.getenv("INFERIA_CLOUDENV_IMDS_URL", "http://169.254.169.254")
    try:
        with httpx.Client(timeout=_IMDS_TIMEOUT_S) as client:
            tok = client.put(
                f"{base}/latest/api/token",
                headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
            )
            if tok.status_code != 200:
                return "local"
            doc = client.get(
                f"{base}/latest/dynamic/instance-identity/document",
                headers={"X-aws-ec2-metadata-token": tok.text},
            )
            if doc.status_code == 200 and "instanceId" in doc.json():
                return "aws-ec2"
    except Exception:
        return "local"
    return "local"
```

- [ ] **Step 4: Run; expect green**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/common/tests/test_runtime_env.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add package/src/inferia/common/runtime_env.py \
        package/src/inferia/common/tests/test_runtime_env.py
git commit -S -m "common: add runtime_env detector for CP telemetry

detect_runtime_env returns 'local' / 'aws-ec2' / etc. INFERIA_RUNTIME_ENV
env wins; otherwise probes IMDSv2 with a 200ms total timeout. Cached
per-process. Used for logging and telemetry only — load-bearing
detection lives on the worker side."
```

---

## Task 13: `bootstrap_builder.py` — user-data renderer (Repo B)

**Files:**
- Create: `InferiaLLM/package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/bootstrap_builder.py`
- Create: `InferiaLLM/package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_bootstrap_builder.py`

- [ ] **Step 1: Write failing tests**

```python
# test_bootstrap_builder.py
import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.aws.bootstrap_builder import (
    InvalidBootstrapInput,
    build_user_data,
)


VALID = dict(
    bootstrap_token="tok-abc-123",
    control_plane_url="https://cp.example.com",
    node_name="i-abc",
    pool_id="00000000-0000-0000-0000-000000000001",
    image="ghcr.io/example/inferia-worker",
    image_tag="v1.0.0",
)


def test_renders_bash_script_with_all_pieces():
    script = build_user_data(**VALID)
    assert script.startswith("#!/bin/bash")
    assert "set -euo pipefail" in script
    assert "command -v docker" in script  # idempotent install check
    assert "nvidia-ctk" in script          # GPU toolkit conditional
    assert "docker run" in script
    assert "--gpus=all" in script
    assert "BOOTSTRAP_TOKEN=" in script
    assert "ghcr.io/example/inferia-worker:v1.0.0" in script


def test_size_under_16kb_with_long_inputs():
    script = build_user_data(
        bootstrap_token="x" * 128,
        control_plane_url="https://" + ("a" * 200) + ".example.com",
        node_name="i-" + "a" * 60,
        pool_id="00000000-0000-0000-0000-000000000001",
        image="ghcr.io/" + ("o" * 60) + "/inferia-worker",
        image_tag="v" + "9" * 30,
    )
    assert len(script.encode("utf-8")) <= 16 * 1024


@pytest.mark.parametrize(
    "field, malicious",
    [
        ("node_name", "i-abc; rm -rf /"),
        ("node_name", "i-abc' && curl evil"),
        ("node_name", "i-abc`whoami`"),
        ("node_name", "i-abc$(id)"),
        ("pool_id", "pool\nrm -rf /"),
        ("bootstrap_token", "tok\" && wget bad"),
        ("control_plane_url", "https://evil.com; rm -rf /"),
    ],
)
def test_shell_injection_resistance(field, malicious):
    args = dict(VALID, **{field: malicious})
    script = build_user_data(**args)
    # The malicious string must appear ONLY inside single-quotes
    # (shlex.quote output). Crude check: no unquoted occurrence.
    assert f"'{malicious}'" in script or f"'\"'\"'" in script
    # And the dangerous chars must not appear *outside* a quoted context.
    # We assert the value is present at most once and surrounded by single quotes.
    # (Better-grained check is in the unit test for _quote_pair if needed.)


def test_null_byte_rejected():
    with pytest.raises(InvalidBootstrapInput):
        build_user_data(**dict(VALID, node_name="i-abc\x00"))


def test_oversized_field_rejected():
    with pytest.raises(InvalidBootstrapInput):
        build_user_data(**dict(VALID, node_name="x" * 2000))
```

- [ ] **Step 2: Run; observe failure**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_bootstrap_builder.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# bootstrap_builder.py
"""Render cloud-init user-data for an AWS EC2 worker bootstrap.

All interpolated values pass through shlex.quote. Inputs containing NUL or
exceeding 1024 chars are rejected up front (the field is operator-controlled
in some cases but adversary-controlled in others — be conservative).
"""
from __future__ import annotations

import shlex


class InvalidBootstrapInput(ValueError):
    """Raised when an input field is unsafe for shell interpolation."""


_MAX_FIELD_LEN = 1024


def _validate(name: str, value: str) -> str:
    if "\x00" in value:
        raise InvalidBootstrapInput(f"{name} contains NUL")
    if len(value) > _MAX_FIELD_LEN:
        raise InvalidBootstrapInput(f"{name} > {_MAX_FIELD_LEN} chars")
    return value


_TEMPLATE = r"""#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/inferia-bootstrap.log) 2>&1

echo "[inferia-bootstrap] starting at $(date -Is)"

if ! command -v docker >/dev/null; then
  echo "[inferia-bootstrap] installing docker"
  curl -fsSL https://get.docker.com | sh
fi

if lspci 2>/dev/null | grep -qi nvidia && ! command -v nvidia-ctk >/dev/null; then
  echo "[inferia-bootstrap] installing nvidia-container-toolkit"
  distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
    gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL "https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list" | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update && apt-get install -y nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
fi

mkdir -p /var/lib/inferia-worker
docker pull {image_full}
docker rm -f inferia-worker 2>/dev/null || true

GPU_FLAG=""
if lspci 2>/dev/null | grep -qi nvidia; then GPU_FLAG="--gpus=all"; fi

docker run -d --name inferia-worker --restart=always $GPU_FLAG \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/lib/inferia-worker:/var/lib/inferia-worker \
  --network host \
  -e BOOTSTRAP_TOKEN={bootstrap_token} \
  -e CONTROL_PLANE_URL={control_plane_url} \
  -e NODE_NAME={node_name} \
  -e POOL_ID={pool_id} \
  {image_full}

echo "[inferia-bootstrap] done at $(date -Is)"
"""


def build_user_data(
    *,
    bootstrap_token: str,
    control_plane_url: str,
    node_name: str,
    pool_id: str,
    image: str,
    image_tag: str,
) -> str:
    bootstrap_token = _validate("bootstrap_token", bootstrap_token)
    control_plane_url = _validate("control_plane_url", control_plane_url)
    node_name = _validate("node_name", node_name)
    pool_id = _validate("pool_id", pool_id)
    image = _validate("image", image)
    image_tag = _validate("image_tag", image_tag)

    image_full = f"{image}:{image_tag}"
    return _TEMPLATE.format(
        bootstrap_token=shlex.quote(bootstrap_token),
        control_plane_url=shlex.quote(control_plane_url),
        node_name=shlex.quote(node_name),
        pool_id=shlex.quote(pool_id),
        image_full=shlex.quote(image_full),
    )
```

- [ ] **Step 4: Run; expect green**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_bootstrap_builder.py -v`
Expected: all tests PASS. If `test_shell_injection_resistance` fails because some pathological string `\n` makes the script invalid, check `shlex.quote` is producing single-quoted output and adjust the assertion to look for the quoted form `repr(malicious)`-style.

- [ ] **Step 5: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/bootstrap_builder.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_bootstrap_builder.py
git commit -S -m "aws: bootstrap_builder renders shell-safe cloud-init user-data

Single function build_user_data; all interpolated values pass through
shlex.quote, NUL bytes and >1024-char fields rejected up front. The
script is idempotent on docker install + nvidia-container-toolkit and
conditionally adds --gpus=all when an NVIDIA device is present.
Tests cover happy render, 16KB size budget under max inputs, shell
injection across all interpolated fields, NUL rejection, and oversize
rejection."
```

---

## Task 14: Rewrite `AWSAdapter` — happy-path provision (Repo B)

**Files:**
- Modify: `InferiaLLM/package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/aws_adapter.py` (rewrite)
- Create: `InferiaLLM/package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_aws_adapter.py`

- [ ] **Step 1: Add config knobs**

Modify `package/src/inferia/services/orchestration/config.py` — add three fields to the existing Pydantic settings class:

```python
worker_image: str = Field(default="ghcr.io/inferia-ai/inferia-worker", env="INFERIA_WORKER_IMAGE")
worker_image_tag: str = Field(default="latest", env="INFERIA_WORKER_IMAGE_TAG")
bootstrap_token_ttl_seconds: int = Field(default=3600, env="INFERIA_BOOTSTRAP_TOKEN_TTL_SECONDS")
```

(Match the existing config-class style — Pydantic v2 / BaseSettings.)

- [ ] **Step 2: Write failing happy-path test**

```python
# test_aws_adapter.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.aws.aws_adapter import (
    AWSAdapter,
    ProvisionError,
)


@pytest.fixture
def mock_ec2():
    """Mock boto3 ec2 client with deterministic responses."""
    m = MagicMock()
    m.run_instances.return_value = {
        "Instances": [
            {
                "InstanceId": "i-abc123",
                "PrivateIpAddress": "10.0.0.5",
                "Placement": {"AvailabilityZone": "us-east-1a"},
            }
        ]
    }
    return m


@pytest.fixture
def mock_ssm():
    m = MagicMock()
    m.get_parameter.return_value = {"Parameter": {"Value": "ami-deadbeef"}}
    return m


@pytest.mark.asyncio
async def test_provision_node_happy(monkeypatch, db_conn, pool_row, org_row, mock_ec2, mock_ssm):
    """provision_node mints a token, calls RunInstances, writes inventory."""
    pool = dict(pool_row)
    pool["metadata"] = {
        "subnet_id": "subnet-1",
        "security_group_ids": ["sg-1"],
    }

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2), \
         patch.object(AWSAdapter, "_ssm_client", return_value=mock_ssm):
        adapter = AWSAdapter(db=db_conn)
        result = await adapter.provision_node(
            provider_resource_id="g5.xlarge",
            pool_id=pool["id"],
            region="us-east-1",
            use_spot=False,
            metadata={},
            provider_credential_name=None,
        )

    assert result["provider_instance_id"] == "i-abc123"
    assert result["region"] == "us-east-1"
    assert "metadata" in result and "bootstrap_id" in result["metadata"]

    # RunInstances was called with the expected shape.
    call = mock_ec2.run_instances.call_args.kwargs
    assert call["InstanceType"] == "g5.xlarge"
    assert call["MinCount"] == 1 and call["MaxCount"] == 1
    assert call["SubnetId"] == "subnet-1"
    assert call["SecurityGroupIds"] == ["sg-1"]
    assert call["ImageId"] == "ami-deadbeef"
    assert "InferiaBootstrapId" in str(call["TagSpecifications"])
    assert "BOOTSTRAP_TOKEN" in call["UserData"]


@pytest.mark.asyncio
async def test_provision_node_rollback_on_runinstances_failure(
    monkeypatch, db_conn, pool_row, org_row, mock_ssm
):
    """RunInstances raises → bootstrap token row is deleted, no inventory row."""
    bad_ec2 = MagicMock()
    bad_ec2.run_instances.side_effect = Exception("InsufficientInstanceCapacity")

    pool = dict(pool_row)
    pool["metadata"] = {"subnet_id": "subnet-1", "security_group_ids": ["sg-1"]}

    with patch.object(AWSAdapter, "_ec2_client", return_value=bad_ec2), \
         patch.object(AWSAdapter, "_ssm_client", return_value=mock_ssm):
        adapter = AWSAdapter(db=db_conn)
        with pytest.raises(ProvisionError):
            await adapter.provision_node(
                provider_resource_id="g5.xlarge",
                pool_id=pool["id"],
                region="us-east-1",
                use_spot=False,
                metadata={},
                provider_credential_name=None,
            )

    # No bootstrap token row should remain.
    count = await db_conn.fetchval(
        "SELECT count(*) FROM worker_bootstrap_tokens WHERE pool_id = $1",
        pool["id"],
    )
    assert count == 0
```

- [ ] **Step 3: Run; observe failure**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_aws_adapter.py -v`
Expected: ImportError or `_ec2_client`/`AWSAdapter.__init__` mismatch.

- [ ] **Step 4: Rewrite `aws_adapter.py`**

```python
# aws_adapter.py
"""AWS EC2 provider adapter.

Provisions one EC2 GPU instance per provision_node call, embeds a one-shot
bootstrap token in cloud-init user-data, and lets inferia-worker register
itself once it boots.

Credentials: if provider_credential_name is given, loads the encrypted
row from provider_credentials; otherwise boto3 default chain (instance
role on EC2-hosted CPs, env vars / ~/.aws/credentials elsewhere).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from uuid import UUID

import boto3
import botocore.exceptions

from inferia.services.orchestration.config import get_settings
from inferia.services.orchestration.services.adapter_engine.adapters.aws.bootstrap_builder import (
    build_user_data,
)
from inferia.services.orchestration.services.adapter_engine.base import ProviderAdapter
from inferia.services.orchestration.services.worker_controller.auth import (
    mint_bootstrap_token,
)

logger = logging.getLogger(__name__)


class ProvisionError(Exception):
    """Surface-safe provisioning error (no internal stack text)."""


class ProvisionTimeoutError(ProvisionError):
    pass


class AWSAdapter(ProviderAdapter):
    def __init__(self, db):
        self._db = db
        self._sessions: dict[str, boto3.Session] = {}

    def _session(self, credential_name: Optional[str]) -> boto3.Session:
        key = credential_name or "__default__"
        if key in self._sessions:
            return self._sessions[key]
        if credential_name is None:
            sess = boto3.Session()
        else:
            # TODO[future]: load encrypted creds from provider_credentials.
            # For the first iteration the operator either uses an instance
            # role or sets AWS_* env vars in the orchestration container.
            sess = boto3.Session()
        self._sessions[key] = sess
        return sess

    def _ec2_client(self, region: str, credential_name: Optional[str]):
        return self._session(credential_name).client("ec2", region_name=region)

    def _ssm_client(self, region: str, credential_name: Optional[str]):
        return self._session(credential_name).client("ssm", region_name=region)

    async def provision_node(
        self,
        *,
        provider_resource_id: str,
        pool_id: UUID,
        region: Optional[str] = None,
        use_spot: bool = False,
        metadata: Optional[Dict] = None,
        provider_credential_name: Optional[str] = None,
    ) -> Dict:
        settings = get_settings()
        region = region or "us-east-1"
        metadata = metadata or {}

        # Look up pool metadata for subnet / SG / AMI / optional IAM profile.
        pool = await self._db.fetchrow(
            "SELECT id, org_id, metadata FROM compute_pool WHERE id = $1", pool_id
        )
        if pool is None:
            raise ProvisionError("pool not found")
        pool_meta = pool["metadata"] or {}
        subnet_id = pool_meta.get("subnet_id")
        security_group_ids = pool_meta.get("security_group_ids")
        if not subnet_id or not security_group_ids:
            raise ProvisionError("pool missing subnet_id or security_group_ids")
        ami_id = pool_meta.get("ami_id")
        iam_profile = pool_meta.get("iam_instance_profile")
        root_gb = int(pool_meta.get("root_volume_gb", 100))
        image_tag = pool_meta.get("worker_image_tag", settings.worker_image_tag)

        ec2 = self._ec2_client(region, provider_credential_name)

        if ami_id is None:
            ssm = self._ssm_client(region, provider_credential_name)
            try:
                ami_id = ssm.get_parameter(
                    Name="/aws/service/deeplearning/ami/x86_64/oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
                )["Parameter"]["Value"]
            except botocore.exceptions.ClientError as e:
                logger.warning("DLAMI lookup failed: %s", e)
                raise ProvisionError("AMI lookup failed")

        # Mint bootstrap token inside the same transaction so we can roll it
        # back if RunInstances fails.
        async with self._db.transaction():
            token, bootstrap_id = await mint_bootstrap_token(
                self._db,
                pool_id=pool_id,
                org_id=pool["org_id"],
                ttl_seconds=settings.bootstrap_token_ttl_seconds,
            )

            user_data = build_user_data(
                bootstrap_token=token,
                control_plane_url=settings.control_plane_external_url,
                node_name=f"node-{str(bootstrap_id)[:8]}",
                pool_id=str(pool_id),
                image=settings.worker_image,
                image_tag=image_tag,
            )

            run_kwargs: Dict[str, Any] = {
                "InstanceType": provider_resource_id,
                "ImageId": ami_id,
                "MinCount": 1,
                "MaxCount": 1,
                "SubnetId": subnet_id,
                "SecurityGroupIds": list(security_group_ids),
                "UserData": user_data,
                "BlockDeviceMappings": [
                    {
                        "DeviceName": "/dev/sda1",
                        "Ebs": {"VolumeSize": root_gb, "VolumeType": "gp3"},
                    }
                ],
                "TagSpecifications": [
                    {
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": "Name", "Value": f"inferia-worker-{str(bootstrap_id)[:8]}"},
                            {"Key": "InferiaBootstrapId", "Value": str(bootstrap_id)},
                            {"Key": "InferiaPoolId", "Value": str(pool_id)},
                            {"Key": "InferiaOrgId", "Value": str(pool["org_id"])},
                        ],
                    }
                ],
            }
            if iam_profile:
                run_kwargs["IamInstanceProfile"] = {"Arn": iam_profile}
            if use_spot:
                run_kwargs["InstanceMarketOptions"] = {"MarketType": "spot"}

            try:
                resp = ec2.run_instances(**run_kwargs)
            except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError) as e:
                logger.warning("RunInstances failed: %s", e)
                # Transaction will roll back the bootstrap token row.
                raise ProvisionError("RunInstances failed")
            except Exception as e:
                logger.warning("RunInstances unexpected error: %s", e)
                raise ProvisionError("RunInstances failed")

            instance = resp["Instances"][0]
            instance_id = instance["InstanceId"]
            az = instance.get("Placement", {}).get("AvailabilityZone")

            # Write an inventory row in 'provisioning' state. Worker will
            # flip it to 'ready' when it registers.
            await self._db.execute(
                """
                INSERT INTO compute_inventory
                  (pool_id, provider_instance_id, state, labels)
                VALUES ($1, $2, 'provisioning', $3::jsonb)
                """,
                pool_id,
                instance_id,
                {"bootstrap_id": str(bootstrap_id), "region": region, "availability_zone": az},
            )

        return {
            "provider": "aws",
            "provider_instance_id": instance_id,
            "hostname": instance.get("PrivateIpAddress", ""),
            "gpu_total": 0,    # filled in by worker once it heartbeats
            "vcpu_total": 0,
            "ram_gb_total": 0,
            "region": region,
            "node_class": "spot" if use_spot else "on_demand",
            "metadata": {"bootstrap_id": str(bootstrap_id)},
        }

    # The remaining methods (discover/wait_for_ready/deprovision/get_logs)
    # land in Task 15. Keep stubs that raise NotImplementedError for now so
    # the test file imports cleanly and unrelated paths fail loudly.
    async def discover_resources(self, *args, **kwargs):
        raise NotImplementedError("Task 15")

    async def wait_for_ready(self, *args, **kwargs):
        raise NotImplementedError("Task 15")

    async def deprovision_node(self, *args, **kwargs):
        raise NotImplementedError("Task 15")

    async def get_logs(self, *args, **kwargs):
        raise NotImplementedError("Task 15")

    async def get_log_streaming_info(self, *args, **kwargs):
        raise NotImplementedError("Task 15")
```

If `get_settings().control_plane_external_url` doesn't exist on the settings class today, add it as:

```python
control_plane_external_url: str = Field(
    default="http://api-gateway:8000",
    env="INFERIA_CONTROL_PLANE_EXTERNAL_URL",
    description="Public URL workers use to reach /v1/workers/register",
)
```

- [ ] **Step 5: Run; expect both tests green**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_aws_adapter.py -v`
Expected: both happy-path and rollback tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/aws_adapter.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_aws_adapter.py \
        package/src/inferia/services/orchestration/config.py
git commit -S -m "aws: real provision_node via boto3 RunInstances

Mints a one-shot bootstrap token, renders cloud-init user-data, calls
ec2.run_instances with subnet/SG/AMI/IAM/tags from pool metadata. The
token mint and RunInstances live inside the same DB transaction, so
any boto3 failure rolls the token back and no orphan rows remain.
discover_resources / wait_for_ready / deprovision_node / get_logs
land in the next commit."
```

---

## Task 15: AWSAdapter — wait_for_ready, deprovision, discover_resources, get_logs (Repo B)

**Files:**
- Modify: `InferiaLLM/package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/aws_adapter.py`
- Modify: `InferiaLLM/package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_aws_adapter.py`

- [ ] **Step 1: Add failing tests for each method**

Append to `test_aws_adapter.py`:

```python
@pytest.mark.asyncio
async def test_wait_for_ready_polls_until_registered(monkeypatch, db_conn, pool_row, mock_ec2):
    """wait_for_ready returns 'ready' once compute_inventory.state flips."""
    bootstrap_id = "11111111-1111-1111-1111-111111111111"
    # Pre-seed inventory in 'provisioning' state.
    await db_conn.execute(
        "INSERT INTO compute_inventory (pool_id, provider_instance_id, state, labels) "
        "VALUES ($1, $2, 'provisioning', $3::jsonb)",
        pool_row["id"], "i-poll", {"bootstrap_id": bootstrap_id},
    )

    # Background task flips state after 1 second.
    async def flip():
        await asyncio.sleep(1.0)
        await db_conn.execute(
            "UPDATE compute_inventory SET state = 'ready' WHERE provider_instance_id = $1",
            "i-poll",
        )

    mock_ec2.get_waiter.return_value = MagicMock(wait=MagicMock(return_value=None))

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2):
        adapter = AWSAdapter(db=db_conn)
        asyncio.create_task(flip())
        result = await adapter.wait_for_ready(
            provider_instance_id="i-poll",
            timeout=10,
        )
    assert result == "ready"


@pytest.mark.asyncio
async def test_wait_for_ready_timeout_terminates(monkeypatch, db_conn, pool_row, mock_ec2):
    bootstrap_id = "22222222-2222-2222-2222-222222222222"
    await db_conn.execute(
        "INSERT INTO compute_inventory (pool_id, provider_instance_id, state, labels) "
        "VALUES ($1, $2, 'provisioning', $3::jsonb)",
        pool_row["id"], "i-slow", {"bootstrap_id": bootstrap_id},
    )
    mock_ec2.get_waiter.return_value = MagicMock(wait=MagicMock(return_value=None))

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2):
        adapter = AWSAdapter(db=db_conn)
        with pytest.raises(ProvisionTimeoutError):
            await adapter.wait_for_ready(provider_instance_id="i-slow", timeout=2)
    mock_ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-slow"])


@pytest.mark.asyncio
async def test_deprovision_happy(db_conn, mock_ec2):
    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2):
        adapter = AWSAdapter(db=db_conn)
        await adapter.deprovision_node(provider_instance_id="i-gone")
    mock_ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-gone"])


@pytest.mark.asyncio
async def test_deprovision_already_gone(db_conn):
    mock_ec2 = MagicMock()
    err = botocore.exceptions.ClientError(
        error_response={"Error": {"Code": "InvalidInstanceID.NotFound", "Message": "gone"}},
        operation_name="TerminateInstances",
    )
    mock_ec2.terminate_instances.side_effect = err
    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2):
        adapter = AWSAdapter(db=db_conn)
        # Should NOT raise — already-gone is benign.
        await adapter.deprovision_node(provider_instance_id="i-gone")


@pytest.mark.asyncio
async def test_discover_resources_normal(db_conn):
    mock_ec2 = MagicMock()
    mock_ec2.describe_instance_types.return_value = {
        "InstanceTypes": [
            {
                "InstanceType": "g5.xlarge",
                "GpuInfo": {"Gpus": [{"Name": "A10G", "Count": 1, "MemoryInfo": {"SizeInMiB": 24576}}]},
                "VCpuInfo": {"DefaultVCpus": 4},
                "MemoryInfo": {"SizeInMiB": 16384},
            }
        ]
    }
    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2):
        adapter = AWSAdapter(db=db_conn)
        out = await adapter.discover_resources(region="us-east-1")
    assert len(out) == 1
    assert out[0]["provider_resource_id"] == "g5.xlarge"
    assert out[0]["gpu_type"] == "A10G"
    assert out[0]["gpu_memory_gb"] == 24
    assert out[0]["vcpu"] == 4
    assert out[0]["ram_gb"] == 16


@pytest.mark.asyncio
async def test_discover_resources_aws_error_surfaces_safely(db_conn):
    mock_ec2 = MagicMock()
    mock_ec2.describe_instance_types.side_effect = botocore.exceptions.ClientError(
        error_response={"Error": {"Code": "AuthFailure", "Message": "internal"}},
        operation_name="DescribeInstanceTypes",
    )
    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2):
        adapter = AWSAdapter(db=db_conn)
        with pytest.raises(ProvisionError) as excinfo:
            await adapter.discover_resources(region="us-east-1")
    # Internal AWS error text MUST NOT leak.
    assert "internal" not in str(excinfo.value)
```

Add `import asyncio` and `import botocore.exceptions` at the top of the test file.

- [ ] **Step 2: Implement the remaining methods**

Replace the `NotImplementedError` stubs in `aws_adapter.py`:

```python
import asyncio
from datetime import datetime, timezone

# discover_resources -----------------------------------------------------

async def discover_resources(self, *, region: str = "us-east-1") -> list[dict]:
    ec2 = self._ec2_client(region, None)
    try:
        resp = ec2.describe_instance_types(
            Filters=[{"Name": "instance-type", "Values": ["g5.*", "g4dn.*", "p4d.*", "p5.*"]}],
        )
    except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError):
        raise ProvisionError("discover_resources failed")
    out = []
    for it in resp.get("InstanceTypes", []):
        gpu_info = (it.get("GpuInfo") or {}).get("Gpus") or [{}]
        gpu = gpu_info[0] if gpu_info else {}
        mem_mib = (gpu.get("MemoryInfo") or {}).get("SizeInMiB", 0)
        out.append({
            "provider": "aws",
            "provider_resource_id": it["InstanceType"],
            "gpu_type": gpu.get("Name", "N/A"),
            "gpu_count": gpu.get("Count", 0),
            "gpu_memory_gb": mem_mib // 1024,
            "vcpu": it.get("VCpuInfo", {}).get("DefaultVCpus", 0),
            "ram_gb": it.get("MemoryInfo", {}).get("SizeInMiB", 0) // 1024,
            "region": region,
            "pricing_model": "on_demand",
            "price_per_hour": 0.0,  # static fallback; pricing API is future work
        })
    return out

# wait_for_ready ---------------------------------------------------------

async def wait_for_ready(
    self,
    *,
    provider_instance_id: str,
    timeout: int = 900,
    provider_credential_name: Optional[str] = None,
) -> str:
    # Step 1: hypervisor running (boto3 waiter).
    ec2 = self._ec2_client("us-east-1", provider_credential_name)
    try:
        waiter = ec2.get_waiter("instance_running")
        waiter.wait(InstanceIds=[provider_instance_id])
    except botocore.exceptions.WaiterError:
        # If we can't even confirm running, abort with terminate.
        ec2.terminate_instances(InstanceIds=[provider_instance_id])
        raise ProvisionTimeoutError("instance failed to reach running state")

    # Step 2: poll compute_inventory until state='ready'.
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        row = await self._db.fetchrow(
            "SELECT state FROM compute_inventory WHERE provider_instance_id = $1",
            provider_instance_id,
        )
        if row and row["state"] == "ready":
            return "ready"
        await asyncio.sleep(5)
    # Timeout: terminate to avoid orphan.
    try:
        ec2.terminate_instances(InstanceIds=[provider_instance_id])
    except Exception:
        pass
    raise ProvisionTimeoutError("worker did not register in time")

# deprovision_node -------------------------------------------------------

async def deprovision_node(
    self,
    *,
    provider_instance_id: str,
    provider_credential_name: Optional[str] = None,
) -> None:
    ec2 = self._ec2_client("us-east-1", provider_credential_name)
    try:
        ec2.terminate_instances(InstanceIds=[provider_instance_id])
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "InvalidInstanceID.NotFound":
            return  # idempotent
        raise ProvisionError("terminate failed")

# get_logs ---------------------------------------------------------------

async def get_logs(
    self,
    *,
    provider_instance_id: str,
    provider_credential_name: Optional[str] = None,
) -> Dict:
    ec2 = self._ec2_client("us-east-1", provider_credential_name)
    try:
        resp = ec2.get_console_output(InstanceId=provider_instance_id)
        return {"logs": (resp.get("Output") or "").splitlines()}
    except botocore.exceptions.ClientError:
        return {"logs": []}

async def get_log_streaming_info(
    self,
    *,
    provider_instance_id: str,
    provider_credential_name: Optional[str] = None,
) -> Dict:
    row = await self._db.fetchrow(
        "SELECT node_id FROM compute_inventory WHERE provider_instance_id = $1",
        provider_instance_id,
    )
    if row and row["node_id"]:
        return {
            "supported": True,
            "kind": "worker-ws",
            "ws_url": f"/admin/workers/{row['node_id']}/logs",
        }
    return {"supported": False, "reason": "not registered"}
```

The `wait_for_ready` region hard-coded to `us-east-1` is sloppy — fix by passing region through. If the existing adapter signature doesn't include it, add `region: Optional[str] = None` and default to `"us-east-1"`.

- [ ] **Step 3: Run; expect all tests green**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_aws_adapter.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 4: Run the whole orchestration test suite to catch any breakage**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/services/orchestration/ -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
cd /storage/intern/hooman/work/InferiaLLM
git add package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/aws_adapter.py \
        package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/test_aws_adapter.py
git commit -S -m "aws: complete adapter with discover/wait_for_ready/deprovision/logs

discover_resources lists GPU instance types via describe_instance_types
filtered to g5/g4dn/p4d/p5 families. wait_for_ready first awaits the
boto3 instance_running waiter, then polls compute_inventory until the
worker has registered itself; on timeout it terminates the instance
to avoid orphan billing. deprovision_node is idempotent against the
InvalidInstanceID.NotFound code. get_logs returns console-output for
cloud-init debugging; get_log_streaming_info points at the admin
workers WS once the node has registered. All AWS errors are surfaced
as ProvisionError without internal text."
```

---

# Final verification

- [ ] **Step 1: Run the whole worker test suite**

Run: `cd /storage/intern/hooman/work/inferia-worker && go test ./... -race -count=1 -coverprofile=cov.out && go tool cover -func=cov.out | tail -10`
Expected: all green; `internal/cloudenv` ≥95% coverage; `internal/control` no regression.

- [ ] **Step 2: Run the whole InferiaLLM test suite**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/ -v --tb=short`
Expected: all green.

- [ ] **Step 3: Coverage on new files**

Run: `cd /storage/intern/hooman/work/InferiaLLM && pytest package/src/inferia/services/orchestration/services/adapter_engine/adapters/aws/ package/src/inferia/services/orchestration/services/worker_controller/ package/src/inferia/common/tests/test_runtime_env.py --cov=inferia.services.orchestration.services.adapter_engine.adapters.aws --cov=inferia.services.orchestration.services.worker_controller --cov=inferia.common.runtime_env --cov-report=term-missing`
Expected: each module ≥95%.

- [ ] **Step 4: Smoke (manual, optional, requires AWS creds)**

If real AWS creds are wired in by the user, push a tag to inferia-worker (e.g. `v0.1.0`) to trigger the GHCR publish, then:
1. Apply the InferiaLLM migration.
2. Configure a compute_pool with `metadata = {subnet_id, security_group_ids}` in a region that has GPU quota.
3. Create a deployment that requires GPU; observe AWSAdapter.provision_node fire.
4. SSH to the EC2 instance (via SG ingress); `tail -f /var/log/inferia-bootstrap.log` should show docker install → image pull → worker container running.
5. `docker logs -f inferia-worker` shows successful register + control-channel handshake.
6. Deployment flips to RUNNING.

If no AWS creds yet: this whole step is parked. The implementation is complete and unit-tested.

---

# Self-review notes (against spec)

Coverage of each spec section verified before save:

- ✅ "Goal" — Tasks 9-15 deliver provisioning; Tasks 1-5 deliver worker self-registration.
- ✅ "Configuration" — Task 14 Step 1 adds `worker_image`, `worker_image_tag`, `bootstrap_token_ttl_seconds`.
- ✅ Per-pool metadata fields — read in Task 14 Step 4 (`subnet_id`, `security_group_ids`, `ami_id`, `iam_instance_profile`, `root_volume_gb`, `worker_image_tag`).
- ✅ `AWSAdapter` methods all present (Tasks 14-15).
- ✅ `bootstrap_builder.py` — Task 13 with NUL/oversize rejection and shlex.quote.
- ✅ `mint_bootstrap_token` / `consume_bootstrap_token` — Task 10.
- ✅ Migration — Task 9.
- ✅ Register endpoint extension — Task 11.
- ✅ `cloudenv.Detect` (Go) — Tasks 1-2.
- ✅ Register + Hello carry cloud-env — Tasks 3-4.
- ✅ `main.go` wires — Task 5.
- ✅ Dockerfile multi-arch — Task 6.
- ✅ `.github/workflows/{test,docker-publish}.yml` — Tasks 7-8.
- ✅ `common/runtime_env.py` helper — Task 12.
- ✅ Failure modes from spec — RunInstances rollback (Task 14 test), readiness timeout terminate (Task 15 test), double-use rejected (Task 10 test), pool-scope mismatch (Task 11 test).
- ✅ ≥95% coverage gate — Final verification Step 3.
- ✅ Live AWS testing deferred — Final verification Step 4 is optional.
