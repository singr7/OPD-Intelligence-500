"""LLMProvider — non-voice LLM calls (doc 02 §2/§5).

Gemini Flash primary, OpenAI gpt-4o-mini fallback, both behind one interface and
selected by config. Used for chief-complaint routing (S4), summaries (S5),
dictation mapping (S10), check-in personalisation (S17), and as the dialogue
brain of voice tier V2 — which is why this interface speaks the intake tool
contract (`app.prompts.tools`) and not just text.

**Why the raw HTTP APIs and not the vendor SDKs.** Two SDKs, two release
cadences, two auth models, and both would still need wrapping to satisfy doc 02
§9. The request shapes below are small and stable; `httpx` is already a
dependency. The cost of this choice is that we track the wire format ourselves —
see the per-impl notes on what to check when a version bumps.

Token accounting is not optional here: `usage_metadata` (Gemini) / `usage`
(OpenAI) is what makes the S18 cost-per-intake number true. When a vendor omits
it, we say so rather than estimate — a made-up token count reconciles to nothing.
"""

from __future__ import annotations

import json
import logging
from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from app.models.enums import UsagePurpose
from app.prompts.tools import ToolSpec
from app.providers.base import Provider, ProviderBadRequest, ProviderUnavailable
from app.providers.metering import MeterCall, UsageDelta

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model's request to call one of our tools. Vendor-neutral."""

    name: str
    arguments: dict[str, Any]
    call_id: str | None = None  # OpenAI needs this echoed back; Gemini does not


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """One completion. `prompt_ref` is the `id@vN` of the prompt that built this
    (see `app.prompts.loader`), carried so an output can be traced to its exact
    prompt version months later."""

    prompt: str
    system: str = ""
    prompt_ref: str | None = None
    max_tokens: int = 1024
    # Low by default: every prompt in `prompts/` wants faithfulness to the
    # patient's words, not variety. V2 dialogue turns raise it slightly.
    temperature: float = 0.1
    json_output: bool = False
    tools: Sequence[ToolSpec] = ()
    # Prior turns as (role, text); V2 dialogue passes the conversation so far.
    history: Sequence[tuple[str, str]] = ()


@dataclass(frozen=True, slots=True)
class LLMResult:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    #: False when the vendor gave us no usage numbers — the cost row is a floor,
    #: not a fact, and S18 should not quietly average it in as truth.
    usage_reported: bool = True

    def json(self) -> Any:
        """Parse a JSON response, tolerating a code fence.

        Models wrap JSON in ```json despite being told not to. Being strict here
        buys nothing: the fence is not a semantic error, and failing a patient's
        intake over three backticks is a bad trade.
        """
        text = self.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderBadRequest(f"model did not return JSON: {self.text[:200]}") from exc


class LLMProvider(Provider):
    """Text in, text (and/or tool calls) out."""

    kind: ClassVar[str] = "llm"
    model: ClassVar[str] = ""

    async def complete(
        self, request: LLMRequest, *, purpose: UsagePurpose = UsagePurpose.OTHER
    ) -> LLMResult:
        return await self._invoke(
            purpose, lambda call: self._complete(request, call), model=self.model
        )

    @abstractmethod
    async def _complete(self, request: LLMRequest, call: MeterCall) -> LLMResult:
        """Run one completion and report tokens on `call`."""


@dataclass
class FakeLLMScript:
    """What the fake should say. Deterministic — tests assert on exact output."""

    text: str = "ok"
    tool_calls: tuple[ToolCall, ...] = ()
    tokens_in: int = 120
    tokens_out: int = 40
    cached_tokens: int = 0


#: Canned JSON for prompts whose callers cannot use the word "ok" (S10).
#:
#: The fakes exist so a whole flow can be demonstrated without a vendor — that
#: is what makes the offline demo and the Playwright runs possible. A fake that
#: answers "ok" to a `response_format: json` prompt can only ever demonstrate
#: the failure path, so the ones with a strict output contract get a canned,
#: contract-shaped reply here. Keyed by prompt id (the part of `prompt_ref`
#: before `@`), so a new prompt version keeps working until it changes shape.
#:
#: These are demo fixtures, not test fixtures: tests that assert on content
#: queue their own `FakeLLMScript`, which always wins over this.
_CANNED_JSON: dict[str, str] = {
    "dictation_map": json.dumps(
        {
            "diagnosis": "Carcinoma breast, post chemotherapy cycle 3, febrile neutropenia",
            "treatment_events": [
                {
                    "cycle": 4,
                    "regimen": "AC-T",
                    "date": None,
                    "next_due": None,
                    "as_spoken": "next cycle 14 tareekh ko",
                }
            ],
            "meds": [
                {
                    "name": "Inj Monocef 1 gm",
                    "dose": "1 g",
                    "route": "IV",
                    "freq": "BD",
                    "duration": "5 days",
                    "known": True,
                    "as_spoken": "Inj Monocef one gram IV BD five days",
                },
                {
                    "name": "Tab Dolo 650",
                    "dose": "650 mg",
                    "route": "PO",
                    "freq": "SOS",
                    "duration": "",
                    "known": True,
                    "as_spoken": "Tab Dolo 650 SOS",
                },
                # Off-formulary on purpose: a demo where nothing is ever flagged
                # teaches the wrong thing about this screen.
                {
                    "name": "Inj Ipilimumab 3 mg/kg",
                    "dose": "3 mg/kg",
                    "route": "IV",
                    "freq": "3-weekly",
                    "duration": "4 doses",
                    "known": True,
                    "as_spoken": "Inj Ipilimumab 3 mg per kg three weekly",
                },
            ],
            "advice": ["Repeat CBC before the next cycle", "Come back at once if the fever climbs"],
            "follow_up": {
                "when": None,
                "as_spoken": "14 tareekh",
                "instructions": "Next chemotherapy cycle",
            },
            "unclear": [],
        }
    ),
}


class FakeLLMProvider(LLMProvider):
    """Deterministic LLM. Never touches the network.

    Default behaviour returns a fixed reply with plausible token counts, so the
    metering path is exercised end to end (S3 AC: every fake call produces a
    priced usage_event) — except for the prompts in `_CANNED_JSON`, which get a
    contract-shaped reply so a demo can run the whole way through. Tests that
    care about content queue scripts, and a queued script always wins.
    """

    name: ClassVar[str] = "fake-llm"
    model: ClassVar[str] = "fake-llm-1"

    def __init__(self, *, script: Sequence[FakeLLMScript] = (), **kwargs) -> None:
        super().__init__(**kwargs)
        self.calls: list[LLMRequest] = []
        self._script: list[FakeLLMScript] = list(script)
        #: Set to raise on the next call — how tests drive fallback and breaker paths.
        self.fail_with: Exception | None = None

    def queue(self, *scripts: FakeLLMScript) -> None:
        self._script.extend(scripts)

    async def _complete(self, request: LLMRequest, call: MeterCall) -> LLMResult:
        self.calls.append(request)
        if self.fail_with is not None:
            raise self.fail_with

        if self._script:
            step = self._script.pop(0)
        else:
            prompt_id = (request.prompt_ref or "").split("@")[0]
            canned = _CANNED_JSON.get(prompt_id)
            step = FakeLLMScript(text=canned) if canned else FakeLLMScript()
        call.usage = UsageDelta(
            tokens_in=step.tokens_in, tokens_out=step.tokens_out, cached_tokens=step.cached_tokens
        )
        return LLMResult(
            text=step.text,
            model=self.model,
            tokens_in=step.tokens_in,
            tokens_out=step.tokens_out,
            cached_tokens=step.cached_tokens,
            tool_calls=step.tool_calls,
            finish_reason="stop",
        )

    @property
    def last(self) -> LLMRequest | None:
        return self.calls[-1] if self.calls else None


def _to_gemini_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    """Our contract → Gemini `functionDeclarations`.

    Gemini's schema dialect is OpenAPI-flavoured and rejects `additionalProperties`,
    so it is stripped here rather than omitted from the contract — our own
    validation (S5) still wants it.
    """

    def clean(schema: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in schema.items() if k != "additionalProperties"}

    return [
        {
            "functionDeclarations": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": clean(t.parameters),
                }
                for t in tools
            ]
        }
    ]


def _to_openai_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


class GeminiFlashProvider(LLMProvider):
    """Gemini Flash via `generateContent` (doc 02 §2: the primary, on cost).

    Wire-format notes, for the next version bump:
    - system prompt goes in `systemInstruction`, not as a message role;
    - tool calls come back as `functionCall` parts, not a separate field;
    - `usageMetadata.cachedContentTokenCount` is absent unless caching is on.
    """

    name: ClassVar[str] = "gemini-flash"
    model: ClassVar[str] = "gemini-2.5-flash"

    BASE_URL: ClassVar[str] = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(configured=bool(api_key), **kwargs)
        self._api_key = api_key
        if model:
            self.model = model
        self._client = client or httpx.AsyncClient(
            base_url=self.BASE_URL, timeout=self.timeout_seconds
        )

    def _payload(self, request: LLMRequest) -> dict[str, Any]:
        contents = [
            {"role": "user" if role == "user" else "model", "parts": [{"text": text}]}
            for role, text in request.history
        ]
        contents.append({"role": "user", "parts": [{"text": request.prompt}]})

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": request.max_tokens,
                "temperature": request.temperature,
            },
        }
        if request.system:
            payload["systemInstruction"] = {"parts": [{"text": request.system}]}
        if request.json_output:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        if request.tools:
            payload["tools"] = _to_gemini_tools(request.tools)
        return payload

    async def _complete(self, request: LLMRequest, call: MeterCall) -> LLMResult:
        try:
            response = await self._client.post(
                f"/models/{self.model}:generateContent",
                json=self._payload(request),
                headers={"x-goog-api-key": self._api_key},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"gemini transport error: {exc}") from exc

        if response.status_code in (400, 404):
            raise ProviderBadRequest(f"gemini rejected the request: {response.text[:200]}")
        if response.status_code >= 300:
            raise ProviderUnavailable(f"gemini http {response.status_code}: {response.text[:200]}")

        body = response.json()
        usage = body.get("usageMetadata") or {}
        tokens_in = int(usage.get("promptTokenCount", 0))
        tokens_out = int(usage.get("candidatesTokenCount", 0))
        cached = int(usage.get("cachedContentTokenCount", 0))
        call.usage = UsageDelta(tokens_in=tokens_in, tokens_out=tokens_out, cached_tokens=cached)

        candidates = body.get("candidates") or []
        if not candidates:
            # Safety blocks land here: no candidate, only promptFeedback.
            raise ProviderUnavailable(f"gemini returned no candidate: {str(body)[:200]}")

        parts = candidates[0].get("content", {}).get("parts", []) or []
        text = "".join(part.get("text", "") for part in parts)
        tool_calls = tuple(
            ToolCall(name=fc["name"], arguments=dict(fc.get("args") or {}))
            for part in parts
            if (fc := part.get("functionCall"))
        )

        return LLMResult(
            text=text,
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cached_tokens=cached,
            tool_calls=tool_calls,
            finish_reason=candidates[0].get("finishReason"),
            usage_reported=bool(usage),
        )


class OpenAIProvider(LLMProvider):
    """OpenAI chat completions — the configured fallback (doc 02 §2).

    Deliberately the same *interface*, not the same *behaviour*: a fallback that
    silently produces differently-shaped summaries would be worse than an outage,
    so the prompts in `prompts/` are written to be vendor-neutral and the tool
    contract is identical. What differs is latency and price, both metered.
    """

    name: ClassVar[str] = "openai"
    model: ClassVar[str] = "gpt-4o-mini"

    BASE_URL: ClassVar[str] = "https://api.openai.com/v1"

    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(configured=bool(api_key), **kwargs)
        self._api_key = api_key
        if model:
            self.model = model
        self._client = client or httpx.AsyncClient(
            base_url=self.BASE_URL, timeout=self.timeout_seconds
        )

    def _auth_headers(self) -> dict[str, str]:
        """Auth for the completions call. A hook so an OpenAI-compatible local
        server (vLLM, `app.providers.local_oss.llm`) can reuse this whole wire
        path and just drop the header when it runs without a key."""
        return {"Authorization": f"Bearer {self._api_key}"}

    def _payload(self, request: LLMRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(
            {"role": "user" if role == "user" else "assistant", "content": text}
            for role, text in request.history
        )
        messages.append({"role": "user", "content": request.prompt})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.json_output:
            payload["response_format"] = {"type": "json_object"}
        if request.tools:
            payload["tools"] = _to_openai_tools(request.tools)
        return payload

    async def _complete(self, request: LLMRequest, call: MeterCall) -> LLMResult:
        try:
            response = await self._client.post(
                "/chat/completions",
                json=self._payload(request),
                headers=self._auth_headers(),
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"{self.name} transport error: {exc}") from exc

        if response.status_code == 400:
            raise ProviderBadRequest(f"{self.name} rejected the request: {response.text[:200]}")
        if response.status_code >= 300:
            raise ProviderUnavailable(
                f"{self.name} http {response.status_code}: {response.text[:200]}"
            )

        body = response.json()
        usage = body.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens", 0))
        tokens_out = int(usage.get("completion_tokens", 0))
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        call.usage = UsageDelta(
            # OpenAI counts cached tokens *inside* prompt_tokens; ours are additive
            # (see pricing._quantities), so subtract to avoid billing them twice.
            tokens_in=max(tokens_in - cached, 0),
            tokens_out=tokens_out,
            cached_tokens=cached,
        )

        choices = body.get("choices") or []
        if not choices:
            raise ProviderUnavailable(f"openai returned no choice: {str(body)[:200]}")
        message = choices[0].get("message") or {}

        tool_calls = tuple(
            ToolCall(
                name=tc["function"]["name"],
                arguments=json.loads(tc["function"].get("arguments") or "{}"),
                call_id=tc.get("id"),
            )
            for tc in (message.get("tool_calls") or [])
        )

        return LLMResult(
            text=message.get("content") or "",
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cached_tokens=cached,
            tool_calls=tool_calls,
            finish_reason=choices[0].get("finish_reason"),
            usage_reported=bool(usage),
        )
