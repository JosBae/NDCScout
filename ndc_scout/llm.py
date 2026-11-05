"""Minimal Meta Model API client (OpenAI-compatible Chat Completions) with cost guards.

What is confirmed vs. assumed (see docs/END-TO-END.md for how to check):
  * Base URL https://api.meta.ai/v1, endpoint /chat/completions, env var
    META_API_KEY, OpenAI-style `tools`/`tool_calls`: documented by LiteLLM's
    and promptfoo's Meta provider pages. Meta's own docs (dev.meta.ai/docs)
    could not be fetched from the build environment.
  * `Authorization: Bearer <key>`: ASSUMED (OpenAI-compatible convention).
    `ndc-scout check-api` tells you immediately if this is wrong.

Why the cost guards: Meta's API is pay-as-you-go with no hard spend cap
(meta-ai-field-notes day-zero checklist). This client refuses to start a call
once a run's budget or a local monthly cap is reached, and logs every call's
token usage to $NDC_SCOUT_HOME/usage.jsonl.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

DEFAULT_BASE = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"

# USD per 1M tokens. Snapshot copied from omenking/meta-costcenter pricing.json (MIT),
# retrieved 2026-10-24 from https://dev.meta.ai/docs/pricing-rate-limits. Re-check before relying on it.
PRICING = {
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.2": (1.25, 0.15, 4.25),
    "muse-spark-1.1": (1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
    "muse-spark-1.2-contributor": (0.10, 0.002, 0.20),
    "muse-wonder-1.0": (4.0, 0.20, 20.0),
}
UNKNOWN_MODEL_RATE = (4.0, 0.20, 20.0)  # price unknown models at the most expensive known rate


class MetaAPIError(RuntimeError):
    def __init__(self, kind: str, message: str, status: int | None = None, fix: str = ""):
        super().__init__(message)
        self.kind, self.status, self.fix = kind, status, fix

    def as_dict(self) -> dict:
        return {"kind": self.kind, "status": self.status, "message": str(self), "fix": self.fix}


class BudgetExceeded(RuntimeError):
    pass


def home() -> Path:
    p = Path(os.environ.get("NDC_SCOUT_HOME", Path.home() / ".ndc-scout"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def estimate_cost(model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    inp, cached, out = PRICING.get(model, UNKNOWN_MODEL_RATE)
    uncached = max(input_tokens - cached_tokens, 0)
    return (uncached * inp + cached_tokens * cached + output_tokens * out) / 1_000_000


def month_to_date_spend(mode: str = "live") -> float:
    log = home() / "usage.jsonl"
    if not log.exists():
        return 0.0
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    total = 0.0
    for line in log.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("mode") == mode and str(rec.get("ts", "")).startswith(month):
            total += float(rec.get("cost_usd") or 0)
    return total


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0

    def as_dict(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cached_tokens": self.cached_tokens, "cost_usd": round(self.cost_usd, 6)}


@dataclass
class MetaClient:
    api_key: str | None = None
    base_url: str = DEFAULT_BASE
    model: str = DEFAULT_MODEL
    mode: str = "live"                      # "live" or "mock" (mock never counts toward the monthly cap)
    max_budget_usd: float = 0.50            # per agent run
    monthly_cap_usd: float | None = None    # local hard stop, e.g. 140 to stay inside $150 credits
    timeout_s: float = 120.0
    http: httpx.Client | None = None
    usage: Usage = field(default_factory=Usage)
    reasoning_effort: str | None = None

    @classmethod
    def from_env(cls, **overrides) -> "MetaClient":
        cap = os.environ.get("NDC_SCOUT_MONTHLY_CAP_USD")
        kw = dict(api_key=os.environ.get("META_API_KEY"),
                  base_url=os.environ.get("META_API_BASE", DEFAULT_BASE).rstrip("/"),
                  model=os.environ.get("META_MODEL", DEFAULT_MODEL),
                  max_budget_usd=float(os.environ.get("NDC_SCOUT_MAX_RUN_USD", "0.50")),
                  monthly_cap_usd=float(cap) if cap else None,
                  reasoning_effort=os.environ.get("META_REASONING_EFFORT") or None)
        kw.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**kw)

    def _check_budget(self) -> None:
        if self.usage.cost_usd >= self.max_budget_usd:
            raise BudgetExceeded(f"run budget ${self.max_budget_usd:.2f} reached (spent ~${self.usage.cost_usd:.4f})")
        if self.mode == "live" and self.monthly_cap_usd is not None:
            spent = month_to_date_spend("live")
            if spent >= self.monthly_cap_usd:
                raise BudgetExceeded(f"local monthly cap ${self.monthly_cap_usd:.2f} reached (~${spent:.2f} logged this month)")

    def chat(self, messages: list[dict], tools: list[dict] | None = None, **extra) -> dict:
        if not self.api_key:
            raise MetaAPIError("no_key", "META_API_KEY is not set.",
                               fix="Create a key at dev.meta.ai (a payment method is required even with credits), then `export META_API_KEY=...`, or run with --mock.")
        self._check_budget()
        body = {"model": self.model, "messages": messages, "max_completion_tokens": 4096}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        body.update(extra)
        url = f"{self.base_url}/chat/completions"
        client = self.http or httpx.Client(timeout=self.timeout_s)
        t0 = time.monotonic()
        try:
            resp = client.post(url, json=body, headers={"Authorization": f"Bearer {self.api_key}",
                                                        "Content-Type": "application/json"})
        except httpx.HTTPError as e:
            raise MetaAPIError("network", f"could not reach {url}: {e}",
                               fix="Check META_API_BASE and your network/proxy.") from e
        finally:
            if self.http is None:
                client.close()
        latency = time.monotonic() - t0
        if resp.status_code >= 400:
            raise _http_error(resp, url, self.model)
        try:
            data = resp.json()
        except ValueError as e:
            raise MetaAPIError("bad_response", f"non-JSON response from {url}: {resp.text[:200]}") from e
        self._record(data.get("usage") or {}, latency)
        return data

    def _record(self, u: dict, latency: float) -> None:
        inp = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
        out = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
        cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("cached_tokens") or 0)
        cost = estimate_cost(self.model, inp, out, cached) if self.mode == "live" else 0.0
        self.usage.calls += 1
        self.usage.input_tokens += inp
        self.usage.output_tokens += out
        self.usage.cached_tokens += cached
        self.usage.cost_usd += cost
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": self.mode, "model": self.model,
               "host": urlparse(self.base_url).netloc, "input_tokens": inp, "output_tokens": out,
               "cached_tokens": cached, "cost_usd": round(cost, 6), "latency_s": round(latency, 3)}
        with open(home() / "usage.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")


def _http_error(resp: httpx.Response, url: str, model: str) -> MetaAPIError:
    try:
        detail = resp.json()
    except ValueError:
        detail = resp.text[:300]
    s = resp.status_code
    msg = f"{s} from {url}: {json.dumps(detail)[:400] if not isinstance(detail, str) else detail}"
    if s == 401:
        return MetaAPIError("auth", msg, s, "Key missing/invalid/deleted, or the API expects a different auth header. Run `ndc-scout check-api`.")
    if s in (402, 403):
        return MetaAPIError("billing_or_access", msg, s, "Add a payment method on dev.meta.ai Billing (needed even for free credits), claim your $150 hackathon credits, and check the key's project/team.")
    if s == 404:
        return MetaAPIError("not_found", msg, s, f"Wrong base URL or model {model!r} not available to you. `ndc-scout check-api` lists models.")
    if s == 429:
        return MetaAPIError("rate_limited", msg, s, "Slow down / retry later; partner inference (Baseten 2M TPM) is an alternative for open-weights models.")
    if s == 400:
        return MetaAPIError("bad_request", msg, s, "Request shape rejected (e.g. a parameter this model doesn't support). Check the error text.")
    return MetaAPIError("server", msg, s, "Meta-side error; retry.")
