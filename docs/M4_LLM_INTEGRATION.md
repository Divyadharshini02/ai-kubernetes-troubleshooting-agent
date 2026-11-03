# Milestone 4: Optional LLM Integration

**Goal:** add an optional LLM that explains deterministic diagnoses in clearer language, without letting it decide anything.

**Acceptance criterion:** AI results are validated, and the application remains useful when the provider is unavailable.

**Status:** accepted with documented limitations, after testing with a stub provider. It has **not yet been run against the real OpenRouter service**, because no API key is configured. One real-cluster test failed in the final run. See [Final verification results](#final-verification-results) and [Verify with the real provider](#verify-with-the-real-provider).

**The LLM is optional.** The project works fully without an API key. Off by default, and only called when a request asks for it.

## Request flow

```text
Kubernetes cluster
  -> Evidence collection (Milestone 2)
  -> Deterministic diagnosis (Milestone 3)      <- authoritative, and finished at this point
  -> Optional explanation, only with explain=true
       -> Bounded, redacted input for one diagnosis
       -> OpenRouter
       -> Schema validation and grounding checks
  -> Response: the unchanged diagnosis, plus a separate llm section
```

The diagnosis is complete before the LLM is involved. The LLM's only output is text in a separate field, so it cannot add, change or remove a diagnosis.

| File | Job |
|---|---|
| [app/core/config.py](../backend/app/core/config.py) | Reads and validates settings |
| [app/agent/provider.py](../backend/app/agent/provider.py) | Provider interface, so another provider can be added |
| [app/agent/openrouter.py](../backend/app/agent/openrouter.py) | OpenRouter client, with limits on time and size |
| [app/agent/prompt.py](../backend/app/agent/prompt.py) | Builds the bounded input and the system prompt |
| [app/agent/validation.py](../backend/app/agent/validation.py) | Rejects output that fails the checks |
| [app/agent/explainer.py](../backend/app/agent/explainer.py) | Runs the steps, applies limits and handles fallback |
| [app/models/explanation.py](../backend/app/models/explanation.py) | Response schema |

## Set up

The LLM needs nothing beyond a free OpenRouter account. To use it:

1. Create an API key at <https://openrouter.ai/keys>.
2. From the repository root, copy the example file:

   ```powershell
   Copy-Item .env.example .env
   ```

3. Edit `.env`, which Git ignores:

   ```env
   LLM_ENABLED=true
   OPENROUTER_API_KEY=your-real-key
   OPENROUTER_MODEL=nvidia/nemotron-3.5-super-vl:free
   ```

To turn it off, set `LLM_ENABLED=false`. Settings are read on each request, so a restart is not needed.

| Setting | Default | Meaning |
|---|---|---|
| `LLM_ENABLED` | `false` | Must be `true` for any call to be made |
| `OPENROUTER_API_KEY` | Empty | Your key. Server side only |
| `OPENROUTER_MODEL` | Empty | Model to use. Nothing is hardcoded |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Must be HTTPS, except on the local machine |
| `LLM_TIMEOUT_SECONDS` | `20` | Per attempt, from above 0 up to 120 (60 before [Milestone 10](M10_AI_AGENT.md)) |
| `LLM_MAX_RETRIES` | `1` | Retries for temporary failures, from 0 to 3 |

Invalid settings do not stop the backend or lose a diagnosis. The explanation reports `invalid_configuration`, and the log names the setting but not its value.

Save `.env` as UTF-8. Copying the example and editing it in VS Code does this. In Windows PowerShell 5.1, avoid creating it with `>`, which can write UTF-16. A file that cannot be read is also reported as `invalid_configuration`, with the error type in the backend log.

### Choosing a model

On 2026-11-01, OpenRouter's public catalogue listed these free models with structured output support:

| Model | Notes |
|---|---|
| `nvidia/nemotron-3.5-super-vl:free` | The example used here |
| `liquid/lfm-2.5-2.6b:free` | Smaller alternative |

Free models change often, so check the current list:

```powershell
(Invoke-RestMethod https://openrouter.ai/api/v1/models).data |
  Where-Object { $_.pricing.prompt -eq '0' -and $_.supported_parameters -contains 'structured_outputs' } |
  Select-Object -ExpandProperty id
```

Some free listings are not text models, so check a model's page before relying on it. Since [Milestone 10](M10_AI_AGENT.md#free-models-only), only model IDs ending in `:free` are accepted, and a response not confirmed as free is not used.

## The API

```text
GET /api/namespaces/{namespace}/diagnosis?explain=true
```

Without `explain=true`, the LLM is never called and the settings are not read. `explain=true` can be combined with `include_evidence=true`.

This is a query parameter rather than a new endpoint because the explanation must describe the same evidence as the diagnosis. A separate endpoint would collect again, so the two could disagree.

The response is the Milestone 3 report with one added field, `llm`, which is `null` unless an explanation was requested. All the existing fields are unchanged. HTTP errors are also unchanged: 404, 422 and 503 work as in Milestone 3, and no LLM problem ever changes the HTTP status.

### Example: LLM unavailable

The status remains HTTP 200, with the full diagnosis:

```json
{
  "outcome": "problems_found",
  "diagnoses": ["...unchanged Milestone 3 diagnoses..."],
  "llm": {
    "used": false,
    "status": "unavailable",
    "provider": "openrouter",
    "model": null,
    "reason": "disabled",
    "message": "The LLM is disabled. Set LLM_ENABLED=true to use it.",
    "notice": "Generated by an LLM from the deterministic diagnosis. It may contain mistakes. The deterministic diagnoses are authoritative, and nothing here was executed.",
    "explanations": [
      {
        "diagnosis_id": "crash_loop_backoff:evidence-lab/oom-demo/app",
        "source_evidence_ids": ["container:evidence-lab/oom-demo/app"],
        "status": "unavailable",
        "reason": "disabled",
        "message": "The LLM is disabled. Set LLM_ENABLED=true to use it.",
        "content": null
      }
    ]
  }
}
```

### Example: explanation generated

This shows the response structure, using text from the stub provider in the tests. It is not output from a real model.

```json
{
  "llm": {
    "used": true,
    "status": "generated",
    "provider": "openrouter",
    "model": "nvidia/nemotron-3.5-super-vl:free",
    "reason": null,
    "message": null,
    "notice": "Generated by an LLM from the deterministic diagnosis. It may contain mistakes. The deterministic diagnoses are authoritative, and nothing here was executed.",
    "explanations": [
      {
        "diagnosis_id": "crash_loop_backoff:demo/web/app",
        "source_evidence_ids": ["container:demo/web/app", "logs:demo/web/app:current"],
        "status": "generated",
        "reason": null,
        "message": null,
        "content": {
          "explanation": "The container keeps stopping and Kubernetes keeps starting it again.",
          "impact": "The workload may not be able to serve requests while this continues.",
          "evidence_summary": ["The container has restarted repeatedly."],
          "investigation_steps": ["Read the container logs and describe the pod."],
          "limitations": ["The evidence does not establish why the process exits."]
        }
      }
    ]
  }
}
```

`source_evidence_ids` comes from the diagnosis, never from the model.

## Deterministic and generated content

| | `diagnoses` | `llm` |
|---|---|---|
| Produced by | Rules | A model |
| Same result for the same evidence | Yes | No |
| Decides what is wrong | Yes | No |
| Cites evidence | Yes | No, it reuses the diagnosis's citations |
| Available without an API key | Yes | No |

If they ever appear to disagree, the diagnosis is correct by definition.

## Fallback

A problem with the LLM never loses the diagnosis. The response stays HTTP 200, and `llm.reason` says what happened:

| `reason` | Meaning | Retried | Stops further calls |
|---|---|---|---|
| `disabled`, `missing_api_key`, `missing_model`, `invalid_configuration` | Not configured, so no call is made | No | Yes |
| `unauthorized`, `insufficient_credits`, `model_not_found`, `rate_limited` | HTTP 401, 402, 404 or 429 | No | Yes |
| `provider_refused`, `bad_request` | HTTP 403 or 400, or a content refusal | No | No |
| `timeout`, `network_error` | No usable connection | Yes | No |
| `provider_error` | HTTP 408 or 5xx, or an error inside an HTTP 200 body | Yes | No |
| `empty_response` | No content, which can happen while a model warms up | Yes | No |
| `response_truncated`, `response_too_large`, `malformed_response`, `schema_validation_failed` | The response could not be used | No | No |
| `unsupported_claim` | The response failed a grounding check | No | No |
| `limit_reached`, `time_budget_exhausted` | A limit was reached | No | Not applicable |
| `unexpected_error` | A defect in this feature, caught so the diagnosis survives | No | Not applicable |

`llm.status` is `generated` if all diagnoses were explained, `partial` if some were, `unavailable` if none were, and `not_needed` if there was nothing to explain. Provider error text is never passed on.

### Limits

| Limit | Value | Why |
|---|---|---|
| Diagnoses explained per API call | 5 | Each costs at least one provider request |
| Provider requests per API call | 5 x (1 + `LLM_MAX_RETRIES`): 10 by default, 20 at most | Temporary failures are retried, and free accounts can be limited to 50 requests per day |
| Time per attempt | `LLM_TIMEOUT_SECONDS` | Applies however slowly the provider sends |
| Total LLM time per API call | 45 seconds | Keeps the API responsive |
| Input per diagnosis | 12,000 characters, for the whole message | Bounds what leaves the machine |
| Response size | 64 KiB, with compressed replies not accepted | Bounds what is read back |
| Output tokens | 4,000 | Reasoning models need room before they answer |

Confirmed problems are explained first. Others are marked `limit_reached`.

If the input is too large, whole items are removed, never cut mid-text: first evidence records, then recommendations, possible causes, confirmed facts, conflicting evidence and missing evidence, each from the end of its list. The fields identifying the diagnosis always stay. `evidence_omitted` and `diagnosis_items_omitted` tell the model how many were removed.

The time limits hold at every stage of an attempt, including a provider sending one byte at a time. When an attempt runs out of time, the API call moves on immediately. If the provider is still sending its headers at that point, the background thread for that attempt can remain until the provider stops sending, but it holds no response for the user and cannot delay one.

## Security

**What is sent:** one diagnosis and only the evidence records it cites. Environment variable details are left out, and anything omitted for size is counted in the document. Text is redacted a second time and control characters are removed.

**Sending evidence to a third party:** with `explain=true`, names, messages and log excerpts from your cluster go to OpenRouter and its model provider. Free model providers may store or train on prompts. Redaction is best effort. **Only enable this for clusters whose data you are comfortable sharing.**

**Prompt injection:** logs and event messages can contain text written to manipulate the model. Three layers address this, and none is assumed to be perfect:

1. All cluster text is sent as values in one JSON document, and the system prompt contains none of it.
2. The system prompt states that this data is never instructions.
3. Output is checked regardless of what the model was told or tricked into saying.

The real protection is the design: the model has no tools, cannot change a diagnosis, and its text is only ever displayed.

**Output checks:** the structure must match exactly, and any extra field causes rejection. The text is then compared with the exact input the model received:

| Check | Rejects |
|---|---|
| `evidence_identifier` | An evidence ID not in the input |
| `invented_value` | A number or quantity not in the input, such as an exit code or `512Mi` |
| `invented_timestamp` | A date or time not in the input |
| `invented_resource` | A resource name not in the input |
| `invented_url` | A URL not in the input |
| `unsupported_failure_type` | A failure type such as `ImagePullBackOff` that does not belong to this diagnosis |
| `contradicts_diagnosis` | Dismissing a confirmed problem, such as "is healthy" or "false alarm" |
| `claimed_certainty` | "The root cause is", "definitely" |
| `confidence_score` | Percentages, confidence or probability |
| `claimed_action` | "I checked", "I ran" |
| `unsafe_command` | Any kubectl verb other than `get`, `describe`, `logs`, `top`, `explain`, `events`, `version` or `api-resources`. Also anything mentioning secrets, another identity or credential, `--raw`, shell chaining, redirection, or a pipe to anything but a filter such as `grep`, plus other destructive commands and paths to credentials |
| `destructive_step` | Investigation steps saying to delete, restart, scale, edit, apply, exec into, port-forward to, grant or decode |
| `echoed_injection` | Repeating an instruction to ignore rules |
| `sensitive_content` | Text matching a credential pattern |

**Secrets:** the key is held as a `SecretStr`, is only placed in the `Authorization` header, and never reaches the frontend. Redirects are disabled so the key cannot be forwarded. TLS verification is required. Rejected model text and provider error messages are never logged, only codes and check names.

**No execution:** the model has no tools or functions, and a response containing a tool call is rejected. Nothing it says is ever run. The command checks exist because a person might follow a suggestion, not because anything here executes one. They are also pattern checks, so they cannot recognise every unsafe command, and a `kubectl get pod -o yaml` suggestion is allowed even though its output can include environment values set directly in a manifest.

### This does not make the application fully secure

- **Kubernetes access:** [Milestone 7](M7_RBAC_SECURITY.md) gives the backend a read-only ServiceAccount, which has its own [limitations](M7_RBAC_SECURITY.md#security-limitations).
- **No authentication:** the API has no login, which is acceptable only for local use.

## Limitations

- **One timing-sensitive real-cluster test:** the `liveness-demo` probe test can fail depending on when the probe runs. See [Final verification results](#final-verification-results).
- **Not yet verified against OpenRouter:** all tests use a local stub. The request format follows OpenRouter's documentation, but real model behaviour, including how often the output passes validation, is unknown.
- **Checks can be too strict:** a correct sentence can be rejected, for example if it mentions a number that is not in the input, such as Kubernetes's five-minute back-off limit. The result is a fallback, never wrong information. If real use shows this happens often, the checks can be tuned.
- **Checks cannot prove truth:** they catch invented values and known patterns of forbidden claims. A sentence that misinterprets real facts in new wording can pass. This is why generated text is labelled and the diagnosis stays authoritative.
- **Suggestions copied from a diagnosis can be rejected:** Milestone 3's probe recommendations include `kubectl port-forward`, which the output checks refuse. A model that repeats it produces a fallback, with the diagnosis kept. How often this happens is unknown until real use.
- **Free tier limits:** free models can be limited to 20 requests per minute and 50 per day, and may be slow or disappear.
- **Structured output varies:** OpenRouter documents that some providers treat the schema as a strong hint. Non-conforming output is rejected, not repaired.
- **Sequential calls:** diagnoses are explained one at a time, so the LLM stage can add up to 45 seconds to a request, on top of collecting the evidence.
- **No caching:** each `explain=true` request uses quota again.

## Run the tests

From `backend/`:

```powershell
# No cluster needed, and no real provider calls
.\.venv\Scripts\python.exe -m pytest

# Real cluster, with a local stand-in for the provider
.\.venv\Scripts\python.exe -m pytest -m cluster
```

No test contacts OpenRouter. A test fixture points every test at a closed local port, and this overrides any `.env` you have. Tests that need a response use a local stub server, through the real HTTP client.

## Final verification results

Recorded on 2026-11-01, on a local kind cluster, with no real provider calls.

| Check | Result |
|---|---|
| Non-cluster suite | 810 passed, 44 deselected |
| Real-cluster suite, run once | 43 passed, **1 failed** |
| Mutation tests on the code changed last | 59 run: 58 detected, 1 survived, none timed out |
| `evidence-lab` cleanup | Everything removed after the run |
| `sample-app` | Running, and diagnosed as `no_problems_found` |

**The real-cluster failure:** `test_real_failure_is_diagnosed_as_expected[probe_failure/liveness-demo-]` expected the cause "HTTP 404" and received "Nothing was listening on the probe port". The probe rule uses only the newest failure event, and that event reported a refused connection. The likely reason is timing: this pod is restarted whenever its probe fails, so a probe can land while nginx is still starting. This reason is inferred, because the events were not captured. Milestone 4 did not change the probe rule, the collectors or this pod's manifest. The test passed in earlier runs, its failure rate is unknown, and it was left unchanged and not rerun, so **it can fail again**.

**The surviving mutation:** removing the `Accept-Encoding: identity` header changes nothing, because the HTTP library sends that value by default. No test can detect this.

**Limits of the mutation results:**

- These are not one continuous run. Mutations 1 to 14 come from a run that was interrupted, and mutation 50 was rerun after a test was added for it.
- Mutations 1 to 14 were scored by the older runner, which counted any non-zero exit as detected. The others count only real test failures.
- Another 23 mutations, on code that changed only by one comment, were not rerun. Their results come from an earlier full run of 55, with its one survivor rerun and detected after a test was added.

## Verify with the real provider

Once you have a key configured as above, this can make real requests. With the default settings, each call makes at most 10: up to 5 diagnoses, each with one possible retry.

```powershell
curl.exe -s "http://127.0.0.1:8000/api/namespaces/sample-app/diagnosis?explain=true"
```

`sample-app` is healthy, so this returns `status: not_needed` and makes no call. To exercise the provider, create a failure first, then point the request at that namespace:

```powershell
kubectl apply -f backend/tests/fixtures/evidence-lab.yaml
Start-Sleep -Seconds 90
curl.exe -s "http://127.0.0.1:8000/api/namespaces/evidence-lab/diagnosis?explain=true"
kubectl delete namespace evidence-lab
```

Check `llm.used`, `llm.status` and each explanation's `reason`.
