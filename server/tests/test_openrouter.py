"""The OpenRouter provider and its sign-in, with the network stubbed out.

Nothing here reaches openrouter.ai. What is being tested is the half of the
integration that is ours: the encoder, the stream reader that has to reassemble
a tool call from fragments, the error translation the router routes on, and the
PKCE arithmetic -- all of which are wrong in ways that only show up as a turn
that answered with silence.
"""

from __future__ import annotations

import base64
import hashlib
import json

import httpx
import pytest

from app.providers import openrouter_oauth as oauth_module
from app.providers.base import ContextOverflow, Image, Message, ProviderError, ToolCall
from app.providers.openrouter import (
    DEFAULT_MODEL,
    OpenRouterProvider,
    _describe,
    _encode,
    _finish_calls,
    _merge_call,
    _reasoning,
    _translate_error,
)
from app.providers.openrouter_oauth import OAuthFlows, _challenge, _normalise_base
from app.thinking import control_for


def sse(*events: dict) -> bytes:
    """A chat-completions stream, including the pieces that are not events.

    The keep-alive comment and the `[DONE]` sentinel are both real and both
    have broken a reader before, so they are in every fixture rather than in
    one test about them.
    """
    body = [b": OPENROUTER PROCESSING\n\n"]
    body += [f"data: {json.dumps(event)}\n\n".encode() for event in events]
    body.append(b"data: [DONE]\n\n")
    return b"".join(body)


def delta(**fields) -> dict:
    return {"choices": [{"index": 0, "delta": fields}]}


def provider_with(handler) -> OpenRouterProvider:
    """A provider whose HTTP client answers from `handler`.

    The client is replaced rather than injected: it is built in `__init__`
    because every other caller wants a real one, and a constructor argument
    that exists only for this file would be a seam nothing else uses.
    """
    provider = OpenRouterProvider("sk-or-test")
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=provider.base_url
    )
    return provider


# -- encoding -----------------------------------------------------------------


def test_a_tool_result_names_the_call_it_answers():
    encoded = _encode(
        Message(role="tool", content="17 degrees", tool_call_id="call_7", tool_name="weather")
    )
    assert encoded == {
        "role": "tool",
        "tool_call_id": "call_7",
        "content": "17 degrees",
    }


def test_an_assistant_turn_replays_the_asking():
    encoded = _encode(
        Message(
            role="assistant",
            content="",
            tool_calls=(ToolCall(id="call_1", name="clock", arguments={"tz": "UTC"}),),
        )
    )
    assert encoded["content"] is None  # not "", which some upstreams reject
    call = encoded["tool_calls"][0]
    assert call["id"] == "call_1"
    assert call["function"]["name"] == "clock"
    # A string, not an object: this is the one place the format is lossy.
    assert json.loads(call["function"]["arguments"]) == {"tz": "UTC"}


def test_images_become_data_urls_beside_the_text():
    message = Message(
        role="user",
        content="what is this",
        images=(Image(name="a.png", mime="image/png", data=b"\x89PNG"),),
    )
    parts = _encode(message)["content"]
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[-1] == {"type": "text", "text": "what is this"}


def test_a_plain_turn_stays_a_string():
    assert _encode(Message(role="user", content="hello")) == {
        "role": "user",
        "content": "hello",
    }


# -- the reasoning field ------------------------------------------------------


def test_every_shape_of_thinking_control_becomes_one_field():
    assert _reasoning("high") == {"effort": "high"}
    assert _reasoning(4096) == {"max_tokens": 4096}
    assert _reasoning(True) == {"enabled": True}
    assert _reasoning(False)["exclude"] is True
    # A budget under Anthropic's floor is raised to it rather than sent as-is.
    assert _reasoning(10) == {"max_tokens": 1024}


def test_no_control_means_the_field_is_absent():
    # Not "reasoning off": a model that reasons by default should keep doing
    # so when nobody has touched the control.
    assert _reasoning(None) is None
    assert _reasoning("enormous") is None


def test_openrouter_models_are_driven_by_effort():
    assert control_for("openrouter", "anthropic/claude-sonnet-4.5").mode == "effort"
    assert control_for("openrouter", DEFAULT_MODEL).mode == "effort"


# -- tool calls arriving in pieces --------------------------------------------


def test_a_call_is_reassembled_from_its_fragments():
    pending: dict = {}
    _merge_call(pending, {"index": 0, "id": "call_a", "function": {"name": "web_search"}})
    _merge_call(pending, {"index": 0, "function": {"arguments": '{"query": "we'}})
    _merge_call(pending, {"index": 0, "function": {"arguments": 'ather"}'}})
    calls = _finish_calls(pending)
    assert calls == (ToolCall(id="call_a", name="web_search", arguments={"query": "weather"}),)


def test_two_calls_in_one_turn_do_not_merge():
    pending: dict = {}
    _merge_call(pending, {"index": 0, "id": "a", "function": {"name": "one", "arguments": "{}"}})
    _merge_call(pending, {"index": 1, "id": "b", "function": {"name": "two", "arguments": "{}"}})
    _merge_call(pending, {"index": 0, "function": {"arguments": ""}})
    assert [call.name for call in _finish_calls(pending)] == ["one", "two"]


def test_unparseable_arguments_become_an_empty_dict():
    # The skill then raises a clean error about a missing argument, which the
    # model is told and can fix. An exception here would end the turn silently.
    pending: dict = {}
    _merge_call(pending, {"index": 0, "id": "a", "function": {"name": "x", "arguments": "{oops"}})
    assert _finish_calls(pending)[0].arguments == {}


def test_a_call_with_no_name_is_dropped():
    pending: dict = {}
    _merge_call(pending, {"index": 0, "function": {"arguments": "{}"}})
    assert _finish_calls(pending) == ()


# -- streaming ----------------------------------------------------------------


@pytest.mark.anyio
async def test_a_stream_yields_text_reasoning_and_one_done_chunk():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["usage"] == {"include": True}
        assert request.headers["authorization"] == "Bearer sk-or-test"
        return httpx.Response(
            200,
            content=sse(
                delta(reasoning="let me think"),
                delta(content="Hello"),
                delta(content=" there"),
                {
                    "model": "anthropic/claude-sonnet-4.5",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 4},
                },
            ),
        )

    chunks = [c async for c in provider_with(handler).stream([Message("user", "hi")])]
    assert "".join(c.text for c in chunks) == "Hello there"
    assert "".join(c.thinking for c in chunks) == "let me think"
    done = chunks[-1]
    assert done.done and done.prompt_tokens == 11 and done.completion_tokens == 4
    # `openrouter/auto` and fallbacks can answer with something other than what
    # was asked for, so what actually answered rides out with the last chunk.
    assert done.meta["served_model"] == "anthropic/claude-sonnet-4.5"


@pytest.mark.anyio
async def test_tool_calls_ride_out_with_the_done_chunk():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse(
                delta(
                    tool_calls=[
                        {"index": 0, "id": "call_1", "function": {"name": "clock", "arguments": ""}}
                    ]
                ),
                delta(tool_calls=[{"index": 0, "function": {"arguments": '{"tz":"UTC"}'}}]),
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            ),
        )

    chunks = [c async for c in provider_with(handler).stream([Message("user", "time?")])]
    # A caller can only act on a turn's calls once the turn has stopped, so
    # nothing before the final chunk carries any.
    assert all(not c.tool_calls for c in chunks[:-1])
    assert chunks[-1].tool_calls[0].arguments == {"tz": "UTC"}


@pytest.mark.anyio
async def test_an_error_after_a_200_is_still_an_error():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=sse({"error": {"code": 402, "message": "out of credit"}})
        )

    with pytest.raises(ProviderError) as raised:
        async for _chunk in provider_with(handler).stream([Message("user", "hi")]):
            pass
    assert "credit" in str(raised.value).lower()


@pytest.mark.anyio
async def test_streaming_without_a_key_says_so_rather_than_asking():
    provider = OpenRouterProvider("")
    with pytest.raises(ProviderError) as raised:
        async for _chunk in provider.stream([Message("user", "hi")]):
            pass
    assert "not connected" in str(raised.value)


@pytest.mark.anyio
async def test_health_is_false_without_a_key_and_makes_no_request():
    assert await OpenRouterProvider("").health() is False


# -- the catalogue ------------------------------------------------------------


def test_a_catalogue_row_keeps_only_what_the_picker_draws():
    row = _describe(
        {
            "id": "anthropic/claude-sonnet-4.5",
            "name": "Claude Sonnet 4.5",
            "description": "x" * 400,
            "context_length": 200000,
            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": ["tools", "reasoning"],
        }
    )
    assert row["vision"] and row["tools"] and row["reasoning"] and not row["free"]
    assert row["context"] == 200000
    # Kept as the string it arrived as: these are decimals with more precision
    # than a float holds, and only the client turns them into a price.
    assert row["prompt_price"] == "0.000003"
    assert len(row["description"]) == 280


def test_a_model_that_costs_nothing_is_flagged_free():
    assert _describe({"id": "x/y", "pricing": {"prompt": "0", "completion": "0"}})["free"]


@pytest.mark.anyio
async def test_the_listing_always_offers_auto_and_is_cached():
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"data": [{"id": "z/last"}, {"id": "a/first"}]})

    provider = provider_with(handler)
    models = await provider.list_models()
    # `openrouter/auto` is a real model id that the listing does not return, so
    # without this the default the provider ships with is missing from its menu.
    assert models[0]["id"] == DEFAULT_MODEL
    assert [m["id"] for m in models[1:]] == ["a/first", "z/last"]

    await provider.list_models()
    assert calls["n"] == 1  # the second open of the picker is not a second call

    provider.set_api_key("sk-or-other")
    await provider.list_models()
    assert calls["n"] == 2  # a new key sees its own catalogue


# -- errors the router routes on ----------------------------------------------


def test_a_full_context_is_an_overflow_so_the_window_shrinks():
    assert isinstance(
        _translate_error(400, "maximum context length is 8192 tokens"), ContextOverflow
    )


def test_a_rate_limit_is_retryable_and_a_bad_key_is_not():
    assert _translate_error(429, "slow down").retryable is True
    assert _translate_error(401, "bad key").retryable is False
    assert _translate_error(500, "boom").retryable is True


def test_a_refused_key_is_reported_as_something_to_do_about_it():
    assert "Reconnect" in str(_translate_error(401, "unauthorized"))
    assert "credit" in str(_translate_error(402, "insufficient credits"))


# -- the sign-in --------------------------------------------------------------


def test_the_challenge_is_the_unpadded_sha256_of_the_verifier():
    verifier = "abc123"
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert _challenge(verifier) == expected
    assert "=" not in _challenge(verifier)


def test_only_an_address_a_browser_can_return_to_is_accepted():
    assert _normalise_base("http://192.168.1.50:8080/") == "http://192.168.1.50:8080"
    assert _normalise_base("https://courier.example.com") == "https://courier.example.com"
    # The desktop shell's own origin is not somewhere OpenRouter can redirect.
    with pytest.raises(ProviderError):
        _normalise_base("tauri://localhost")
    with pytest.raises(ProviderError):
        _normalise_base("")


def test_the_state_rides_in_the_callback_path_and_the_challenge_in_the_url():
    flows = OAuthFlows()
    flow = flows.begin("http://127.0.0.1:8080")
    assert flow.callback_url == f"http://127.0.0.1:8080/openrouter/callback/{flow.state}"
    url = flows.url_for(flow)
    assert _challenge(flow.verifier) in url
    assert "code_challenge_method=S256" in url
    # The callback is percent-encoded into the query rather than concatenated.
    assert "callback_url=http%3A%2F%2F127.0.0.1%3A8080" in url
    # Neither of the two secrets is in what the client is handed.
    assert "verifier" not in json.dumps(flow.to_dict())
    assert flow.verifier not in url


@pytest.mark.anyio
async def test_a_completed_sign_in_hands_back_a_key_once(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"key": "sk-or-v1-minted"})

    _mock_exchange(monkeypatch, handler)

    flows = OAuthFlows()
    flow = flows.begin("http://127.0.0.1:8080")
    verifier = flow.verifier

    assert await flows.complete(flow.state, "the-code") == "sk-or-v1-minted"
    assert seen == {
        "code": "the-code",
        "code_verifier": verifier,
        "code_challenge_method": "S256",
    }
    # The client is still polling this state, so the flow stays readable --
    # what is spent is the verifier, which is what makes it single-use.
    assert flows.get(flow.state).status == "connected"
    with pytest.raises(ProviderError) as raised:
        await flows.complete(flow.state, "the-code")
    assert "already been used" in str(raised.value)


@pytest.mark.anyio
async def test_a_refused_exchange_is_reported_on_the_flow(monkeypatch):
    _mock_exchange(
        monkeypatch,
        lambda _: httpx.Response(400, json={"error": {"message": "invalid_grant"}}),
    )
    flows = OAuthFlows()
    flow = flows.begin("http://127.0.0.1:8080")
    with pytest.raises(ProviderError):
        await flows.complete(flow.state, "stale-code")
    assert flows.get(flow.state).status == "failed"
    assert flows.get(flow.state).error == "invalid_grant"


@pytest.mark.anyio
async def test_an_unknown_state_cannot_spend_a_code():
    # The state is what stands in for the bearer token on a route a browser
    # arrives at from openrouter.ai, so a guess must not get anywhere.
    with pytest.raises(ProviderError):
        await OAuthFlows().complete("not-a-state", "code")


def test_expired_flows_are_swept(monkeypatch):
    flows = OAuthFlows()
    flow = flows.begin("http://127.0.0.1:8080")
    monkeypatch.setattr(
        oauth_module.time,
        "monotonic",
        lambda: flow.created_at + oauth_module.FLOW_TTL_SECONDS + 1,
    )
    assert flows.get(flow.state) is None


def test_the_flow_table_cannot_grow_without_bound():
    flows = OAuthFlows()
    for _ in range(oauth_module.MAX_FLOWS + 4):
        flows.begin("http://127.0.0.1:8080")
    assert len(flows.flows) <= oauth_module.MAX_FLOWS


def _mock_exchange(monkeypatch, handler) -> None:
    """Point the token exchange at a stub without touching its call site."""
    real = httpx.AsyncClient

    def fake(*args, **kwargs):
        return real(*args, **{**kwargs, "transport": httpx.MockTransport(handler)})

    monkeypatch.setattr(oauth_module.httpx, "AsyncClient", fake)
