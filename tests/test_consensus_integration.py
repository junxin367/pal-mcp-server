"""Integration test for ConsensusTool using OpenAI and Gemini recordings."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from providers.registry import ModelProviderRegistry
from providers.shared import ProviderType
from tests.transport_helpers import inject_transport
from tools.consensus import ConsensusTool

# Directories for recorded HTTP interactions
CASSETTE_DIR = Path(__file__).parent / "openai_cassettes"
CASSETTE_DIR.mkdir(exist_ok=True)

# Mapping of OpenAI model names to their cassette files
CONSENSUS_CASSETTES = {
    "gpt-5.5": CASSETTE_DIR / "consensus_step1_gpt55_for.json",
    "gpt-5.4": CASSETTE_DIR / "consensus_step1_gpt54_for.json",
}

GEMINI_REPLAY_DIR = Path(__file__).parent / "gemini_cassettes"
GEMINI_REPLAY_DIR.mkdir(exist_ok=True)
GEMINI_REPLAY_ID = "consensus/step2_gemini25_flash_against/mldev"
GEMINI_REPLAY_PATH = GEMINI_REPLAY_DIR / "consensus" / "step2_gemini25_flash_against" / "mldev.json"


def test_consensus_recording_models_match_current_openai_catalog(monkeypatch):
    """Keep recorded Consensus model parameters aligned with the built-in OpenAI catalogue."""
    from providers.openai import OpenAIModelProvider

    with monkeypatch.context() as m:
        m.delenv("OPENAI_MODELS_CONFIG_PATH", raising=False)
        OpenAIModelProvider.reload_registry()

        assert set(CONSENSUS_CASSETTES) == {"gpt-5.5", "gpt-5.4"}
        assert set(CONSENSUS_CASSETTES).issubset(OpenAIModelProvider.MODEL_CAPABILITIES)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.no_mock_provider
@pytest.mark.parametrize("openai_model", ["gpt-5.5", "gpt-5.4"])
async def test_consensus_multi_model_consultations(monkeypatch, openai_model):
    """Exercise ConsensusTool against OpenAI model (supporting) and gemini-2.5-flash (critical).

    Tests the current and previous OpenAI flagship models from conf/openai_models.json.
    """

    # Get the cassette path for this model
    consensus_cassette_path = CONSENSUS_CASSETTES[openai_model]

    env_updates = {
        "DEFAULT_MODEL": "auto",
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
    }
    keys_to_clear = [
        "XAI_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "MISTRAL_API_KEY",
        "CUSTOM_API_KEY",
        "CUSTOM_API_URL",
    ]

    recording_mode = not consensus_cassette_path.exists() or not GEMINI_REPLAY_PATH.exists()
    if recording_mode:
        openai_key = env_updates["OPENAI_API_KEY"].strip()
        gemini_key = env_updates["GEMINI_API_KEY"].strip()
        if (not openai_key or openai_key.startswith("dummy")) or (not gemini_key or gemini_key.startswith("dummy")):
            pytest.skip(
                "Consensus cassette missing and OPENAI_API_KEY/GEMINI_API_KEY "
                "not configured. Provide real keys to record."
            )

    GEMINI_REPLAY_PATH.parent.mkdir(parents=True, exist_ok=True)

    with monkeypatch.context() as m:
        m.setenv("DEFAULT_MODEL", env_updates["DEFAULT_MODEL"])

        if recording_mode:
            m.setenv("OPENAI_API_KEY", env_updates["OPENAI_API_KEY"])
            m.setenv("GEMINI_API_KEY", env_updates["GEMINI_API_KEY"])
            m.setenv("GOOGLE_GENAI_CLIENT_MODE", "record")
        else:
            m.setenv("OPENAI_API_KEY", "dummy-key-for-replay")
            m.setenv("GEMINI_API_KEY", "dummy-key-for-replay")
            m.setenv("GOOGLE_GENAI_CLIENT_MODE", "replay")

        # Ensure restriction policies allow the latest OpenAI models under test
        m.setenv("OPENAI_ALLOWED_MODELS", openai_model)

        m.setenv("GOOGLE_GENAI_REPLAYS_DIRECTORY", str(GEMINI_REPLAY_DIR))
        m.setenv("GOOGLE_GENAI_REPLAY_ID", GEMINI_REPLAY_ID)

        for key in keys_to_clear:
            m.delenv(key, raising=False)

        # Ensure we use the built-in OpenAI catalogue rather than leftovers from
        # other tests that patch OPENAI_MODELS_CONFIG_PATH.
        m.delenv("OPENAI_MODELS_CONFIG_PATH", raising=False)

        # Reset providers/restrictions and register only OpenAI & Gemini for deterministic behavior
        ModelProviderRegistry.reset_for_testing()
        import utils.model_restrictions as model_restrictions

        model_restrictions._restriction_service = None
        from providers.gemini import GeminiModelProvider
        from providers.openai import OpenAIModelProvider

        # Earlier tests may override the OpenAI provider's registry by pointing
        # OPENAI_MODELS_CONFIG_PATH at fixtures. Force a reload so model
        # metadata is restored from conf/openai_models.json.
        OpenAIModelProvider.reload_registry()
        assert openai_model in OpenAIModelProvider.MODEL_CAPABILITIES

        ModelProviderRegistry.register_provider(ProviderType.OPENAI, OpenAIModelProvider)
        ModelProviderRegistry.register_provider(ProviderType.GOOGLE, GeminiModelProvider)

        # Inject HTTP transport for OpenAI interactions
        inject_transport(monkeypatch, str(consensus_cassette_path))

        tool = ConsensusTool()

        models_to_consult = [
            {"model": openai_model, "stance": "for"},
            {"model": "gemini-2.5-flash", "stance": "against"},
        ]

        # Step 1 consults both independent models in parallel.
        step1_arguments = {
            "step": "Evaluate SwiftUI vs UIKit adoption and recommend ONE word (SwiftUI or UIKit).",
            "step_number": 1,
            "total_steps": 1,
            "next_step_required": False,
            "findings": "SwiftUI momentum is strong but UIKit remains battle-tested.",
            "models": models_to_consult,
        }

        step1_response = await tool.execute(step1_arguments)
        assert step1_response and step1_response[0].type == "text"
        step1_data = json.loads(step1_response[0].text)

        assert step1_data["status"] == "consensus_workflow_complete"
        assert step1_data["consensus_complete"] is True
        assert step1_data["next_step_required"] is False
        assert [response["model"] for response in step1_data["model_responses"]] == [
            openai_model,
            "gemini-2.5-flash",
        ]
        assert step1_data["model_responses"][0]["status"] == "success"
        assert step1_data["model_responses"][0]["metadata"]["provider"] == "openai"
        assert step1_data["model_responses"][1]["status"] == "success"
        assert step1_data["model_responses"][1]["metadata"]["provider"] == "google"
        assert all(response["verdict"] for response in step1_data["model_responses"])
        assert step1_data["complete_consensus"]["models_consulted"] == [
            f"{openai_model}:for",
            "gemini-2.5-flash:against",
        ]

        continuation_offer = step1_data.get("continuation_offer")
        assert continuation_offer is not None
        continuation_id = continuation_offer["continuation_id"]
        assert continuation_id

    # Ensure Gemini replay session is flushed to disk before verification
    gemini_provider = ModelProviderRegistry.get_provider_for_model("gemini-2.5-flash")
    if gemini_provider is not None:
        try:
            client = gemini_provider.client
            if hasattr(client, "close"):
                client.close()
        finally:
            if hasattr(gemini_provider, "_client"):
                gemini_provider._client = None

    # Ensure cassettes exist for future replays
    assert consensus_cassette_path.exists()
    assert GEMINI_REPLAY_PATH.exists()

    # Clean up provider registry state after test
    ModelProviderRegistry.reset_for_testing()


@pytest.mark.asyncio
@pytest.mark.no_mock_provider
async def test_consensus_auto_mode_with_openrouter_and_gemini(monkeypatch):
    """Ensure continuation flow resolves to real models instead of leaking 'auto'."""

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip() or "dummy-key-for-replay"
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip() or "dummy-key-for-replay"

    with monkeypatch.context() as m:
        m.setenv("DEFAULT_MODEL", "auto")
        m.setenv("GEMINI_API_KEY", gemini_key)
        m.setenv("OPENROUTER_API_KEY", openrouter_key)

        for key in [
            "OPENAI_API_KEY",
            "XAI_API_KEY",
            "DIAL_API_KEY",
            "CUSTOM_API_KEY",
            "CUSTOM_API_URL",
        ]:
            m.delenv(key, raising=False)

        import importlib

        import config

        m.setattr(config, "DEFAULT_MODEL", "auto")

        import server as server_module

        server = importlib.reload(server_module)
        m.setattr(server, "DEFAULT_MODEL", "auto", raising=False)

        ModelProviderRegistry.reset_for_testing()
        from providers.gemini import GeminiModelProvider
        from providers.openrouter import OpenRouterProvider

        ModelProviderRegistry.register_provider(ProviderType.GOOGLE, GeminiModelProvider)
        ModelProviderRegistry.register_provider(ProviderType.OPENROUTER, OpenRouterProvider)

        from utils.storage_backend import get_storage_backend

        # Clear conversation storage to avoid cross-test leakage
        storage = get_storage_backend()
        storage._store.clear()

        models_to_consult = [
            {"model": "claude-3-5-flash-20241022", "stance": "neutral"},
            {"model": "gpt-5-mini", "stance": "neutral"},
        ]

        step1_args = {
            "step": "Evaluate framework options.",
            "step_number": 1,
            "total_steps": 1,
            "next_step_required": False,
            "findings": "Initial analysis requested.",
            "models": models_to_consult,
        }

        step1_output = await server.handle_call_tool("consensus", step1_args)
        assert step1_output and step1_output[0].type == "text"
        step1_payload = json.loads(step1_output[0].text)

        try:
            assert step1_payload["consensus_complete"] is True
            assert [response["model"] for response in step1_payload["model_responses"]] == [
                "claude-3-5-flash-20241022",
                "gpt-5-mini",
            ]
            assert step1_payload["model_responses"][0]["status"] == "error"
            assert "claude-3-5-flash-20241022" in step1_payload["model_responses"][0]["error"]
        finally:
            # Reset provider registry regardless of outcome to avoid cross-test bleed
            ModelProviderRegistry.reset_for_testing()

    serialized = json.dumps(step1_payload)
    assert "auto" not in serialized.lower(), "Auto model leakage should be resolved"
    assert "gpt-5-mini" in serialized or "claude-3-5-flash-20241022" in serialized

    # Restore server module to reflect original configuration for other tests
    import importlib

    import server as server_module

    importlib.reload(server_module)
