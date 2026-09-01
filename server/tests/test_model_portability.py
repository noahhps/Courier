"""Tests verifying tool calling and schema portability across local and cloud models."""

from __future__ import annotations

from app.providers.anthropic import _build_turns
from app.providers.base import Message, ToolCall
from app.providers.ollama import _parse_call
from app.skills.mcp_skill import coerce_arguments, sanitize_tool_name
from app.skills.skill import Skill


class SampleSkill(Skill):
    def __init__(self):
        super().__init__(
            name="get_weather",
            description="Fetch current temperature and conditions",
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "days": {"type": "integer"},
                    "include_forecast": {"type": "boolean"},
                },
                "required": ["city"],
            },
        )

    async def use(self, **kwargs) -> str:
        return f"Weather for {kwargs.get('city')}"


# -- Argument Coercion --------------------------------------------------------


def test_argument_coercion_for_stringified_inputs():
    schema = {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "ratio": {"type": "number"},
            "enabled": {"type": "boolean"},
            "tags": {"type": "array"},
            "meta": {"type": "object"},
            "label": {"type": "string"},
        },
    }

    # Model sent strings for numbers, booleans, and JSON structures
    raw_args = {
        "count": "42",
        "ratio": "3.14",
        "enabled": "true",
        "tags": '["tag1", "tag2"]',
        "meta": '{"key": "value"}',
        "label": "hello",
    }

    coerced = coerce_arguments(raw_args, schema)
    assert coerced["count"] == 42
    assert coerced["ratio"] == 3.14
    assert coerced["enabled"] is True
    assert coerced["tags"] == ["tag1", "tag2"]
    assert coerced["meta"] == {"key": "value"}
    assert coerced["label"] == "hello"


def test_sanitize_tool_names():
    assert sanitize_tool_name("server.read-file") == "server_read-file"
    assert sanitize_tool_name("google/gmail/send") == "google_gmail_send"
    assert sanitize_tool_name("  weird $ symbol @ ") == "weird___symbol"


# -- Schema Generation --------------------------------------------------------


def test_neutral_schema_generation():
    skill = SampleSkill()
    schema = skill.schema()
    assert schema["name"] == "get_weather"
    assert schema["parameters"]["type"] == "object"
    assert "city" in schema["parameters"]["properties"]
    assert schema["parameters"]["required"] == ["city"]


# -- Local Provider (Ollama) Normalization -----------------------------------


def test_ollama_parses_stringified_arguments():
    raw_entry = {
        "id": "call_123",
        "function": {
            "name": "get_weather",
            "arguments": '{"city": "San Francisco", "days": 3}',
        },
    }
    call = _parse_call(raw_entry, index=0)
    assert call.id == "call_123"
    assert call.name == "get_weather"
    assert call.arguments == {"city": "San Francisco", "days": 3}


def test_ollama_parses_dict_arguments():
    raw_entry = {
        "function": {
            "name": "get_weather",
            "arguments": {"city": "Tokyo"},
        }
    }
    call = _parse_call(raw_entry, index=0)
    assert call.id == "call_0"
    assert call.name == "get_weather"
    assert call.arguments == {"city": "Tokyo"}


# -- Cloud Provider (Anthropic) Normalization --------------------------------


def test_anthropic_turns_with_tool_use_and_tool_result():
    messages = [
        Message(role="system", content="Preamble"),
        Message(role="user", content="What is the weather in London?"),
        Message(
            role="assistant",
            content="Let me check.",
            tool_calls=(
                ToolCall(
                    id="toolu_123",
                    name="get_weather",
                    arguments={"city": "London"},
                ),
            ),
        ),
        Message(
            role="tool",
            content="18C, Rain",
            tool_call_id="toolu_123",
            tool_name="get_weather",
        ),
    ]

    turns = _build_turns(messages)

    # 3 turns: user prompt, assistant tool_use, user tool_result
    assert len(turns) == 3

    # Turn 1: user
    assert turns[0]["role"] == "user"
    assert turns[0]["content"] == "What is the weather in London?"

    # Turn 2: assistant with tool_use block
    assert turns[1]["role"] == "assistant"
    assert len(turns[1]["content"]) == 2
    assert turns[1]["content"][0]["type"] == "text"
    assert turns[1]["content"][1]["type"] == "tool_use"
    assert turns[1]["content"][1]["id"] == "toolu_123"
    assert turns[1]["content"][1]["name"] == "get_weather"
    assert turns[1]["content"][1]["input"] == {"city": "London"}

    # Turn 3: tool result mapped to role="user" with tool_result block
    assert turns[2]["role"] == "user"
    assert isinstance(turns[2]["content"], list)
    assert turns[2]["content"][0]["type"] == "tool_result"
    assert turns[2]["content"][0]["tool_use_id"] == "toolu_123"
    assert turns[2]["content"][0]["content"] == "18C, Rain"
