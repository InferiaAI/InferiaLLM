# Inference Gateway

The Inference Gateway is the data plane for InferiaLLM. It handles routing inference requests to upstream model providers, applying policies (rate limiting, quotas, guardrails, PII, RAG), and returning OpenAI-compatible responses.

**Port**: `8001`

---

## Endpoints

### List Models

```
GET /v1/models
```

**Headers**:
| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <token>` |
| `x-sandbox` | No | `"true"` to enable sandbox mode |

**Response**:
```json
{
  "object": "list",
  "data": [
    {
      "id": "model-name",
      "object": "model",
      "owned_by": "organization"
    }
  ]
}
```

---

### Chat Completions

```
POST /v1/chat/completions
```

**Headers**:
| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <token>` |
| `x-sandbox` | No | `"true"` to enable sandbox mode |

**Request Body**:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Model identifier |
| `messages` | array | Yes | Array of `{role, content}` objects |
| `stream` | boolean | No | Enable SSE streaming |
| `temperature` | float | No | Sampling temperature (0.0–2.0) |
| `top_p` | float | No | Nucleus sampling (0.0–1.0) |
| `max_tokens` | integer | No | Max tokens to generate |
| `n` | integer | No | Number of completions |
| `stop` | string/array | No | Stop sequences |
| `presence_penalty` | float | No | Presence penalty |
| `frequency_penalty` | float | No | Frequency penalty |
| `seed` | integer | No | Random seed |
| `tools` | array | No | Tool/function definitions |
| `tool_choice` | string/object | No | Tool selection strategy |
| `response_format` | object | No | Response format spec |

**Response** (non-streaming):
```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "model-name",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello!"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 5,
    "total_tokens": 15
  }
}
```

**Response** (streaming): Server-Sent Events (`text/event-stream`) with delta objects.

---

### Embeddings

```
POST /v1/embeddings
```

**Headers**:
| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <token>` |
| `x-sandbox` | No | `"true"` to enable sandbox mode |

**Request Body**:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Embedding model identifier |
| `input` | string/array | Yes | Text(s) to embed |

**Response**:
```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "embedding": [0.001, -0.002, ...],
      "index": 0
    }
  ],
  "model": "model-name",
  "usage": {
    "prompt_tokens": 10,
    "total_tokens": 10
  }
}
```

---

### Image Generation

```
POST /v1/images/generations
```

**Headers**:
| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <token>` |
| `x-sandbox` | No | `"true"` to enable sandbox mode |

**Request Body**:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Image model identifier |
| `prompt` | string | Yes | Text description |
| `n` | integer | No | Number of images (default 1) |
| `size` | string | No | Image size (e.g. `"512x512"`, `"1280x720"`) |
| `response_format` | string | No | `"url"` or `"b64_json"` |
| `quality` | string | No | `"hd"` or `"standard"` |
| `style` | string | No | Style parameter |
| `step` | integer | No | Diffusion steps |
| `seed` | integer | No | Random seed |
| `mode` | string | No | Generation mode |
| `scheduler` | string | No | Scheduler type |
| `strength` | float | No | Strength parameter |

**Response**:
```json
{
  "created": 1234567890,
  "data": [
    {
      "url": "data:image/png;base64,...",
      "b64_json": "..."
    }
  ]
}
```

---

### Image Edits

```
POST /v1/images/edits
```

**Headers**: Same as image generation.

**Request Body**:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Image model identifier |
| `prompt` | string | Yes | Edit instructions |
| `image` | string | Yes | Base64 encoded source image |
| `mask` | string | No | Base64 mask image |
| `n` | integer | No | Number of results (default 1) |
| `size` | string | No | Output size |
| `strength` | float | No | Edit strength |
| `step` | integer | No | Diffusion steps |
| `seed` | integer | No | Random seed |

**Response**: Same format as image generation.

---

### Image Variations

```
POST /v1/images/variations
```

**Headers**: Same as image generation.

**Request Body**:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Image model identifier |
| `image` | string | Yes | Base64 encoded source image |
| `n` | integer | No | Number of variations (default 1) |
| `size` | string | No | Output size |

**Response**: Same format as image generation.

---

### Video Generation

```
POST /v1/videos/generations
```

Video generation is **asynchronous**. The endpoint returns immediately with a video ID. Poll `GET /v1/videos/{video_id}` for completion.

**Headers**:
| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <token>` |
| `x-sandbox` | No | `"true"` to enable sandbox mode |

**Request Body**:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Video model identifier |
| `prompt` | string | Yes | Text description |
| `n` | integer | No | Number of videos (default 1) |
| `seconds` | integer | No | Video duration (4–20s) |
| `size` | string | No | Resolution (e.g. `"1280x720"`) |
| `input_reference` | string | No | Base64 image for image-to-video |
| `seed` | integer | No | Random seed |
| `step` | integer | No | Diffusion steps |
| `mode` | string | No | Generation mode |
| `scheduler` | string | No | Scheduler type |
| `strength` | float | No | Strength parameter |

**Response**:
```json
{
  "id": "vid_...",
  "status": "processing"
}
```

---

### Video Edits

```
POST /v1/videos/edits
```

**Headers**: Same as video generation.

**Request Body**:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Video model identifier |
| `video` | string | Yes | Base64 encoded source video |
| `prompt` | string | No | Edit instructions |
| `n` | integer | No | Number of results (default 1) |
| `seconds` | integer | No | Output duration |

**Response**: Same as video generation (async, poll for result).

---

### Video Extensions

```
POST /v1/videos/extensions
```

**Headers**: Same as video generation.

**Request Body**:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Video model identifier |
| `video` | string | Yes | Base64 encoded video to extend |
| `prompt` | string | No | Extension guidance |
| `n` | integer | No | Number of results (default 1) |
| `seconds` | integer | No | Additional duration |

**Response**: Same as video generation (async, poll for result).

---

### Video Status (Polling)

```
GET /v1/videos/{video_id}?model=<model_name>
```

Poll this endpoint after initiating video generation, edits, or extensions.

**Path Parameters**:
| Param | Type | Description |
|-------|------|-------------|
| `video_id` | string | Video generation ID from the POST response |

**Query Parameters**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Model name used for the generation |

**Headers**:
| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <token>` |
| `x-sandbox` | No | `"true"` to enable sandbox mode |

**Response** (processing):
```json
{
  "status": "processing",
  "id": "vid_..."
}
```

**Response** (completed):
```json
{
  "object": "video",
  "data": [
    {
      "id": "vid_...",
      "url": "data:video/mp4;base64,...",
      "video_url": "data:video/mp4;base64,..."
    }
  ]
}
```

**Response** (failed): Returns HTTP 500 with error detail.

---

## Error Responses

All endpoints return errors in this format:

```json
{
  "detail": "Error message"
}
```

| Status | Description |
|--------|-------------|
| 400 | Bad request (missing required fields, invalid JSON) |
| 401 | Unauthorized (invalid/missing API key) |
| 404 | Deployment not found for model |
| 429 | Rate limit exceeded (includes `Retry-After` header) |
| 500 | Internal server error / generation failed |
| 502 | Upstream provider error |
| 504 | Request timed out |

---

## Policy Pipeline

Requests pass through these policies (where applicable):

1. **Rate Limiting** — RPM-based per deployment
2. **Quota Check** — User consumption limits
3. **Guardrails** — Input/output safety scanning (text only)
4. **PII Handling** — Anonymize/de-anonymize (text only)
5. **RAG** — Knowledge base context injection (text only)
6. **Prompt Templates** — Template expansion (text only)
7. **Logging** — Background audit logging after response

---

## Supported Providers

| Engine | Type | Format |
|--------|------|--------|
| `openai` | Text | OpenAI native |
| `groq` | Text | OpenAI-compatible |
| `cerebras` | Text | OpenAI-compatible |
| `gemini` | Text | OpenAI-compatible |
| `openrouter` | Text | OpenAI-compatible |
| `anthropic` | Text | Anthropic → OpenAI conversion |
| `cohere` | Text | Cohere → OpenAI conversion |
| `vllm` | Text | OpenAI-compatible |
| `ollama` | Text | OpenAI-compatible |
| `infinity` | Embedding | OpenAI-compatible |
| `tei` | Embedding | OpenAI-compatible |
| `inferia-diffusion` | Image/Video | Custom paths (`/generate/v1/...`) |
