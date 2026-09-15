from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from devpilot.errors import ModelGatewayError
from devpilot import telemetry


@dataclass(frozen=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reported_total_tokens: int | None = None


@dataclass(frozen=True)
class ModelResponse:
    content: str | None = None
    tool_calls: tuple[ModelToolCall, ...] = ()
    usage: ModelUsage = field(default_factory=ModelUsage)

    @classmethod
    def final(cls, value: dict[str, Any] | str, *, prompt_tokens: int = 1, completion_tokens: int = 1):
        content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return cls(content=content, usage=ModelUsage(prompt_tokens, completion_tokens))

    @classmethod
    def tools(cls, calls: list[dict[str, Any]], *, prompt_tokens: int = 1, completion_tokens: int = 1):
        return cls(
            tool_calls=tuple(
                ModelToolCall(
                    call_id=item.get("call_id", f"call_{index}"),
                    name=item["name"],
                    arguments=copy.deepcopy(item.get("arguments", {})),
                )
                for index, item in enumerate(calls)
            ),
            usage=ModelUsage(prompt_tokens, completion_tokens),
        )


class ModelGateway(Protocol):
    def complete(
        self,
        *,
        agent_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_model: type[BaseModel],
        timeout_seconds: int,
        max_completion_tokens: int | None = None,
        node: str | None = None,
        turn: int | None = None,
    ) -> ModelResponse: ...


class ScriptedFakeModelGateway:
    """Scenario-driven test gateway; it performs no inference."""

    def __init__(self, scenario: dict[str, list[ModelResponse | Exception]], *, strict: bool = True):
        self._scenario = {agent: list(responses) for agent, responses in scenario.items()}
        self.strict = strict
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        agent_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_model: type[BaseModel],
        timeout_seconds: int,
        max_completion_tokens: int | None = None,
        node: str | None = None,
        turn: int | None = None,
    ) -> ModelResponse:
        self.calls.append(
            {
                "agent_id": agent_id,
                "message_count": len(messages),
                "messages": copy.deepcopy(messages),
                "tools": [item["function"]["name"] for item in tools],
                "output_model": output_model.__name__,
                "max_completion_tokens": max_completion_tokens,
                "node": node,
                "turn": turn,
            }
        )
        queue = self._scenario.get(agent_id)
        if not queue:
            raise AssertionError(f"no scripted response left for {agent_id}")
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def assert_consumed(self) -> None:
        remaining = {key: len(value) for key, value in self._scenario.items() if value}
        if self.strict and remaining:
            raise AssertionError(f"unconsumed scripted responses: {remaining}")

    def call_count(self, agent_id: str | None = None) -> int:
        return sum(1 for call in self.calls if agent_id is None or call["agent_id"] == agent_id)


def _openai_client_class(*, traced: bool = False):
    """Prefer the Langfuse OpenAI drop-in so model calls are traced automatically.

    Initialize telemetry first; offline usage keeps the normal OpenAI adapter.
    """

    if traced:
        from langfuse.openai import OpenAI
    else:
        from openai import OpenAI
    return OpenAI


class OpenAICompatibleGateway:
    """OpenAI-compatible Chat Completions adapter with structured-output fallback."""

    def __init__(self, *, model: str, base_url: str | None = None, api_key: str | None = None):
        try:
            self.traced = telemetry.telemetry_enabled()
            client_class = _openai_client_class(traced=self.traced)
        except ImportError as exc:  # pragma: no cover - dependency smoke covers this
            raise ModelGatewayError("openai package is required") from exc
        key = api_key or os.environ.get("DEVPILOT_MODEL_API_KEY")
        if not key:
            raise ModelGatewayError("DEVPILOT_MODEL_API_KEY is required")
        self.model = model
        self.client = client_class(
            api_key=key, base_url=base_url or os.environ.get("DEVPILOT_MODEL_BASE_URL")
        )

    @staticmethod
    def _convert(response: Any) -> ModelResponse:
        message = response.choices[0].message
        prompt_tokens = int(getattr(response.usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(
            getattr(response.usage, "completion_tokens", 0) or 0
        )
        reported_total = int(
            getattr(response.usage, "total_tokens", 0)
            or prompt_tokens + completion_tokens
        )
        # OpenAI includes cached input in prompt_tokens and reasoning in
        # completion_tokens. Some compatible gateways report those only in
        # total_tokens, so charge any otherwise-unclassified remainder too.
        completion_tokens += max(
            0,
            reported_total - prompt_tokens - completion_tokens,
        )
        usage = ModelUsage(
            prompt_tokens,
            completion_tokens,
            reported_total,
        )
        calls = []
        for call in message.tool_calls or []:
            arguments = json.loads(call.function.arguments or "{}")
            if call.function.name == "submit_result":
                return ModelResponse(content=json.dumps(arguments, ensure_ascii=False), usage=usage)
            calls.append(ModelToolCall(call.id, call.function.name, arguments))
        return ModelResponse(content=message.content, tool_calls=tuple(calls), usage=usage)

    def complete(
        self,
        *,
        agent_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_model: type[BaseModel],
        timeout_seconds: int,
        max_completion_tokens: int | None = None,
        node: str | None = None,
        turn: int | None = None,
    ) -> ModelResponse:
        schema = output_model.model_json_schema()
        common = {"model": self.model, "messages": messages, "timeout": timeout_seconds}
        if max_completion_tokens is not None:
            common["max_completion_tokens"] = max_completion_tokens
        if tools:
            common["tools"] = tools
        trace_attributes = {
            "name": f"generate-{agent_id}",
            "metadata": {"agent_id": agent_id, "node": node, "turn": turn},
        } if self.traced else {}
        try:
            response = self.client.chat.completions.create(
                **common,
                **trace_attributes,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": output_model.__name__, "strict": True, "schema": schema},
                },
            )
            return self._convert(response)
        except Exception as strict_error:
            submit_tool = {
                "type": "function",
                "function": {"name": "submit_result", "description": "Submit the final structured result", "parameters": schema},
            }
            try:
                fallback = {key: value for key, value in common.items() if key != "tools"}
                response = self.client.chat.completions.create(
                    **fallback, **trace_attributes, tools=[*tools, submit_tool]
                )
                return self._convert(response)
            except Exception:
                try:
                    response = self.client.chat.completions.create(
                        **{key: value for key, value in common.items() if key != "tools"},
                        **trace_attributes,
                        response_format={"type": "json_object"},
                    )
                    return self._convert(response)
                except Exception as exc:
                    raise ModelGatewayError(f"all structured output modes failed: {exc}") from strict_error


class LazyOpenAICompatibleGateway:
    """Defers credential validation so read-only CLI commands work offline."""

    def __init__(self, *, model: str, base_url: str | None = None, api_key: str | None = None):
        self.options = {"model": model, "base_url": base_url, "api_key": api_key}
        self._delegate: OpenAICompatibleGateway | None = None

    def complete(self, **kwargs: Any) -> ModelResponse:
        if self._delegate is None:
            self._delegate = OpenAICompatibleGateway(**self.options)
        return self._delegate.complete(**kwargs)
