# HCBifrost timeout change did not remove the Qwen reset

**Date:** 2026-08-28.
**Probe:** `analysis/probes/litellm-cut-diagnostic.py qwen3.8-max`.

## Change reported by the provider owner

The HCBifrost administrators reported these LiteLLM ingress changes as applied:

- nginx `proxy_read_timeout 130s`;
- nginx `proxy_send_timeout 130s`;
- nginx `proxy_buffering off`.

No HCBifrost configuration artifact or reload output is available in this
repository, so this measurement treats the change as operator-reported and
tests only its externally observable effect.

## Post-change paired control

The existing diagnostic sent the same production-shaped Qwen payload twice in
each mode. Weblate's transport timeout remained 120 seconds and its body-read
deadline remained 300 seconds during this diagnostic.

| Mode | Run | First byte | Total | Result |
| --- | ---: | ---: | ---: | --- |
| non-streaming | 1 | never | 31.8 s | `ConnectionResetError`, zero bytes |
| non-streaming | 2 | never | 31.5 s | `ConnectionResetError`, zero bytes |
| streaming | 1 | 8.8 s | 39.2 s | HTTP 200, 106,927 bytes |
| streaming | 2 | 6.6 s | 40.8 s | HTTP 200, 115,406 bytes |

## Interpretation

The reported nginx change did not remove the externally observed failure.
Both non-streaming calls still reset in the same approximately 30-second band,
while both streaming calls crossed that boundary and completed.

This result rejects the hypothesis that changing only the reported public
nginx directives was sufficient. It does not prove that those directives were
absent or ineffective. The remaining explanations include:

- the change targeted a different nginx server or location;
- the configuration was not loaded by the serving workers;
- another ingress or internal load balancer retains a 30-second idle timeout;
- LiteLLM or its provider adapter enforces the boundary;
- the upstream Qwen route closes the non-streaming connection.

The next provider-side action is a correlated trace for one request through
the public nginx, the internal LiteLLM listener and the direct upstream route.
Until that trace exists, Qwen non-streaming remains NO-GO. Qwen streaming
remains the production candidate, subject to the full production-envelope and
quality gates.
