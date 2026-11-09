"""A small provider-neutral client for structured JSON output from an LLM.

Keys stay in server environment variables. Prompts and responses are never logged
or attached to telemetry; only counts, providers, and outcomes are recorded.
"""

import json
import os
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from opentelemetry import metrics

Provider = Literal["none", "anthropic", "openai"]
PROVIDERS: tuple[Provider, ...] = ("none", "anthropic", "openai")
DEFAULT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
}
ANTHROPIC_VERSION = "2023-06-01"

_meter = metrics.get_meter(__name__)
request_counter = _meter.create_counter("tabletalk.llm.requests")
token_counter = _meter.create_counter("tabletalk.llm.tokens")


class LLMError(RuntimeError):
    """The provider failed or returned something other than the requested JSON."""


@dataclass(frozen=True)
class LLMConfig:
    provider: Provider = "none"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    timeout_seconds: float = 30.0
    # Optional "minimal" / "low" / "medium" for reasoning models (OpenAI GPT-5 family).
    reasoning_effort: str = ""
    problem: str | None = None

    @property
    def enabled(self) -> bool:
        return self.provider != "none" and self.problem is None

    @classmethod
    def from_env(cls) -> "LLMConfig":
        raw = os.getenv("LLM_PROVIDER", "none").strip().lower() or "none"
        if raw not in PROVIDERS:
            return cls(problem=f"Unknown LLM_PROVIDER {raw!r}; use none, anthropic, or openai.")
        provider: Provider = raw
        if provider == "none":
            return cls()
        key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
        model = os.getenv("LLM_MODEL", "").strip()
        api_key = os.getenv(key_name, "").strip()
        try:
            timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
        except ValueError:
            timeout = 30.0
        problem = None
        if not model:
            problem = "Set LLM_MODEL to the provider's model ID."
        elif not api_key:
            problem = f"Set {key_name}."
        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URLS[provider],
            timeout_seconds=timeout,
            reasoning_effort=os.getenv("LLM_REASONING_EFFORT", "").strip().lower(),
            problem=problem,
        )


class LLMClient(Protocol):
    provider: str
    model: str

    async def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]: ...


class _HttpClient:
    provider = "none"

    def __init__(self, config: LLMConfig, http: httpx.AsyncClient) -> None:
        self.model = config.model
        self._config = config
        self._http = http

    async def _post(
        self, url: str, headers: dict[str, str], body: dict[str, Any], timeout: float | None
    ) -> dict[str, Any]:
        try:
            response = await self._http.post(
                url,
                headers=headers,
                json=body,
                timeout=timeout or self._config.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            request_counter.add(1, {"provider": self.provider, "outcome": "timeout"})
            raise LLMError(f"{self.provider} timed out.") from error
        except httpx.RequestError as error:
            request_counter.add(1, {"provider": self.provider, "outcome": "unreachable"})
            raise LLMError(f"{self.provider} is unreachable.") from error
        if response.is_error:
            request_counter.add(1, {"provider": self.provider, "outcome": "http_error"})
            raise LLMError(f"{self.provider} returned HTTP {response.status_code}.")
        try:
            data = response.json()
        except ValueError as error:
            request_counter.add(1, {"provider": self.provider, "outcome": "invalid_body"})
            raise LLMError(f"{self.provider} returned a non-JSON response.") from error
        if not isinstance(data, dict):
            request_counter.add(1, {"provider": self.provider, "outcome": "invalid_body"})
            raise LLMError(f"{self.provider} returned an unexpected response.")
        request_counter.add(1, {"provider": self.provider, "outcome": "ok"})
        return data

    def _count_tokens(self, input_tokens: object, output_tokens: object) -> None:
        for direction, count in (("input", input_tokens), ("output", output_tokens)):
            if isinstance(count, int):
                token_counter.add(count, {"provider": self.provider, "direction": direction})


class AnthropicClient(_HttpClient):
    """Claude Messages API, forcing a single tool call whose input is the JSON we want."""

    provider = "anthropic"

    async def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        data = await self._post(
            f"{self._config.base_url}/v1/messages",
            {
                "x-api-key": self._config.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "tools": [
                    {
                        "name": schema_name,
                        "description": "Return the result in this exact structure.",
                        "input_schema": schema,
                    }
                ],
                "tool_choice": {"type": "tool", "name": schema_name},
            },
            timeout_seconds,
        )
        usage = data.get("usage") or {}
        self._count_tokens(usage.get("input_tokens"), usage.get("output_tokens"))
        for block in data.get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") == schema_name:
                result = block.get("input")
                if isinstance(result, dict):
                    return result
        raise LLMError("anthropic did not return the requested structure.")


class OpenAIClient(_HttpClient):
    """OpenAI-compatible Chat Completions with a strict JSON schema response format."""

    provider = "openai"

    async def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_completion_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            },
        }
        if self._config.reasoning_effort:
            body["reasoning_effort"] = self._config.reasoning_effort
            # Reasoning tokens count against the completion budget.
            body["max_completion_tokens"] = max_tokens * 4
        data = await self._post(
            f"{self._config.base_url}/v1/chat/completions",
            {"authorization": f"Bearer {self._config.api_key}"},
            body,
            timeout_seconds,
        )
        usage = data.get("usage") or {}
        self._count_tokens(usage.get("prompt_tokens"), usage.get("completion_tokens"))
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise LLMError("openai returned no choices.") from error
        if message.get("refusal"):
            raise LLMError("openai refused the request.")
        try:
            result = json.loads(message.get("content") or "")
        except json.JSONDecodeError as error:
            raise LLMError("openai returned invalid JSON.") from error
        if not isinstance(result, dict):
            raise LLMError("openai did not return a JSON object.")
        return result


VECTOR_DIMS = 512


@dataclass(frozen=True)
class EmbeddingConfig:
    """OpenAI-compatible embeddings for semantic matching. Disabled unless configured."""

    model: str = ""
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URLS["openai"]
    timeout_seconds: float = 30.0

    @property
    def enabled(self) -> bool:
        return bool(self.model and self.api_key)

    @classmethod
    def from_env(cls) -> "EmbeddingConfig":
        base_url = DEFAULT_BASE_URLS["openai"]
        if os.getenv("LLM_PROVIDER", "").strip().lower() == "openai":
            base_url = os.getenv("LLM_BASE_URL", "").strip() or base_url
        return cls(
            model=os.getenv("EMBEDDING_MODEL", "").strip(),
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            base_url=base_url,
        )


class Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """Batch text embeddings at a fixed dimension that matches the Elasticsearch mapping."""

    batch_size = 256

    def __init__(self, config: EmbeddingConfig, http: httpx.AsyncClient) -> None:
        self.model = config.model
        self._config = config
        self._http = http

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            try:
                response = await self._http.post(
                    f"{self._config.base_url}/v1/embeddings",
                    headers={"authorization": f"Bearer {self._config.api_key}"},
                    json={"model": self.model, "input": batch, "dimensions": VECTOR_DIMS},
                    timeout=self._config.timeout_seconds,
                )
            except httpx.HTTPError as error:
                request_counter.add(1, {"provider": "openai-embeddings", "outcome": "unreachable"})
                raise LLMError("The embeddings provider is unreachable.") from error
            if response.is_error:
                request_counter.add(1, {"provider": "openai-embeddings", "outcome": "http_error"})
                raise LLMError(f"Embeddings returned HTTP {response.status_code}.")
            try:
                data = response.json()
                rows = sorted(data["data"], key=lambda row: row["index"])
                batch_vectors = [[float(value) for value in row["embedding"]] for row in rows]
            except (ValueError, KeyError, TypeError) as error:
                raise LLMError("Embeddings returned an unexpected response.") from error
            if len(batch_vectors) != len(batch) or any(
                len(vector) != VECTOR_DIMS for vector in batch_vectors
            ):
                raise LLMError("Embeddings returned the wrong number or size of vectors.")
            request_counter.add(1, {"provider": "openai-embeddings", "outcome": "ok"})
            tokens = (data.get("usage") or {}).get("prompt_tokens")
            if isinstance(tokens, int):
                token_counter.add(tokens, {"provider": "openai-embeddings", "direction": "input"})
            vectors.extend(batch_vectors)
        return vectors


def create_embedder(config: EmbeddingConfig, http: httpx.AsyncClient) -> Embedder | None:
    return OpenAIEmbedder(config, http) if config.enabled else None


def create_llm_client(config: LLMConfig, http: httpx.AsyncClient) -> LLMClient | None:
    if not config.enabled:
        return None
    if config.provider == "anthropic":
        return AnthropicClient(config, http)
    return OpenAIClient(config, http)


def strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    """A JSON schema object that both providers' strict structured output modes accept."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


STRING: dict[str, Any] = {"type": "string"}
NULLABLE_STRING: dict[str, Any] = {"type": ["string", "null"]}
STRING_LIST: dict[str, Any] = {"type": "array", "items": STRING}
