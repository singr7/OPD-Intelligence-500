"""LocalLLMProvider — the dialogue + summary brain of V-OSS (doc 08 §2/§5).

vLLM serves an open model (Qwen3-8B-Instruct AWQ, by default) behind an
**OpenAI-compatible** `/v1/chat/completions` endpoint with continuous batching and
function calling. That compatibility is the whole trick: the wire format is
byte-for-byte the OpenAI one, so this adapter is `OpenAIProvider` pointed at a
local `base_url` — same payload building, same tool-call parsing, same token
accounting — with two differences that matter:

- **No API key.** A local vLLM behind the WireGuard tunnel needs no auth; the
  `Authorization` header is dropped (or set to whatever `api_key` you configure if
  you front it with a gateway). It still reports `configured=True` — "is the box
  reachable" is a health question, answered by the breaker, not by a missing key.
- **`provider=local-vllm`.** Usage still meters (doc 02 §9 is not optional for a
  free provider); it prices against the amortized `local-vllm` rows in
  `price_book`, which is what lets the S18 dashboard compare V-OSS cost-per-intake
  against Gemini/OpenAI rather than showing a misleading flat zero.

The prompts are unchanged: `prompts/` is vendor-neutral by design (doc 02 §5), so
the same routing/summary/intake prompts that drive Gemini drive Qwen. Whether an
8B local model is *good enough* for each job is a measured question, not an
assumed one — see doc 08 §7 (dictation→Rx stays on the strongest model until the
S-OSS bench proves the local one on the Hinglish fixtures).
"""

from __future__ import annotations

from typing import ClassVar

import httpx

from app.providers.llm import OpenAIProvider


class LocalLLMProvider(OpenAIProvider):
    """An OpenAI-compatible local model server (vLLM). Config-only, keyless."""

    name: ClassVar[str] = "local-vllm"
    #: The served model id — must match the `--served-model-name` vLLM is launched
    #: with, and the `local-vllm` model in `price_book` (or its `*` row).
    model: ClassVar[str] = "qwen3-8b-awq"

    #: A local box on the LAN/tunnel is faster than a cloud API but not free of a
    #: cold cache; the 10s default is fine, kept explicit so S-OSS.2 can tune it
    #: against the measured first-token latency.
    timeout_seconds: ClassVar[float] = 10.0

    def __init__(
        self,
        *,
        base_url: str,
        model: str | None = None,
        api_key: str = "",
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        # A local server is "configured" the moment we know its URL — unlike a
        # cloud vendor, an empty key is normal, not a misconfiguration.
        self._api_key = api_key
        if model:
            self.model = model
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=self.timeout_seconds)
        # Skip OpenAIProvider.__init__ (it demands the vendor base_url + treats an
        # empty key as unconfigured); wire the Provider base directly instead.
        from app.providers.base import Provider

        Provider.__init__(self, configured=bool(base_url), **kwargs)

    def _auth_headers(self) -> dict[str, str]:
        # Most local vLLM deployments accept any/no bearer token; send one only if
        # the operator put a gateway in front and configured a key.
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
