"""Tests for OpenAI provider implementation."""

import os
from unittest.mock import MagicMock, patch

import pytest

from providers.openai import OpenAIModelProvider
from providers.shared import ProviderType


class TestOpenAIProvider:
    """Test OpenAI provider functionality."""

    def setup_method(self):
        """Set up clean state before each test."""
        # Clear restriction service cache before each test
        import utils.model_restrictions

        utils.model_restrictions._restriction_service = None

    def teardown_method(self):
        """Clean up after each test to avoid singleton issues."""
        # Clear restriction service cache after each test
        import utils.model_restrictions

        utils.model_restrictions._restriction_service = None

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_initialization(self):
        """Test provider initialization."""
        provider = OpenAIModelProvider("test-key")
        assert provider.api_key == "test-key"
        assert provider.get_provider_type() == ProviderType.OPENAI
        assert provider.base_url == "https://api.openai.com/v1"

    def test_initialization_with_custom_url(self):
        """Test provider initialization with custom base URL."""
        provider = OpenAIModelProvider("test-key", base_url="https://custom.openai.com/v1")
        assert provider.api_key == "test-key"
        assert provider.base_url == "https://custom.openai.com/v1"

    def test_model_validation(self):
        """Test model name validation."""
        provider = OpenAIModelProvider("test-key")

        # Test valid models
        assert provider.validate_model_name("gpt-5") is True
        assert provider.validate_model_name("gpt-5-mini") is True
        assert provider.validate_model_name("gpt-5.4") is True
        assert provider.validate_model_name("gpt-5.4-mini") is True

        # Test valid aliases
        assert provider.validate_model_name("mini") is True
        assert provider.validate_model_name("gpt5") is True
        assert provider.validate_model_name("gpt5-mini") is True
        assert provider.validate_model_name("gpt5mini") is True
        assert provider.validate_model_name("gpt5.4") is True
        assert provider.validate_model_name("gpt5.4mini") is True
        assert provider.validate_model_name("gpt5.2") is False
        assert provider.validate_model_name("codex-mini") is False

        # Test invalid model
        assert provider.validate_model_name("o3") is False
        assert provider.validate_model_name("o3-mini") is False
        assert provider.validate_model_name("o3-pro") is False
        assert provider.validate_model_name("o4-mini") is False
        assert provider.validate_model_name("o3mini") is False
        assert provider.validate_model_name("o3pro") is False
        assert provider.validate_model_name("o4mini") is False
        assert provider.validate_model_name("invalid-model") is False
        assert provider.validate_model_name("gpt-4") is False
        assert provider.validate_model_name("gemini-pro") is False

    def test_resolve_model_name(self):
        """Test model name resolution."""
        provider = OpenAIModelProvider("test-key")

        # Test shorthand resolution
        assert provider._resolve_model_name("mini") == "gpt-5.4-mini"
        assert provider._resolve_model_name("gpt5") == "gpt-5.5"
        assert provider._resolve_model_name("gpt5-mini") == "gpt-5.4-mini"
        assert provider._resolve_model_name("gpt5mini") == "gpt-5.4-mini"
        assert provider._resolve_model_name("gpt5.4") == "gpt-5.4"
        assert provider._resolve_model_name("gpt5.4mini") == "gpt-5.4-mini"
        assert provider._resolve_model_name("gpt5.2") == "gpt5.2"
        assert provider._resolve_model_name("codex-mini") == "codex-mini"

        # Test full name passthrough
        assert provider._resolve_model_name("o3") == "o3"
        assert provider._resolve_model_name("o3-mini") == "o3-mini"
        assert provider._resolve_model_name("o3-pro") == "o3-pro"
        assert provider._resolve_model_name("o4-mini") == "o4-mini"
        assert provider._resolve_model_name("o3mini") == "o3mini"
        assert provider._resolve_model_name("o3pro") == "o3pro"
        assert provider._resolve_model_name("o4mini") == "o4mini"
        assert provider._resolve_model_name("gpt-5") == "gpt-5.5"
        assert provider._resolve_model_name("gpt-5-mini") == "gpt-5.4-mini"
        assert provider._resolve_model_name("gpt-5.4") == "gpt-5.4"
        assert provider._resolve_model_name("gpt-5.1") == "gpt-5.1"
        assert provider._resolve_model_name("gpt-5.1-codex") == "gpt-5.1-codex"
        assert provider._resolve_model_name("gpt-5.1-codex-mini") == "gpt-5.1-codex-mini"

    def test_removed_o_series_is_not_available(self):
        """O-series models are API-only and should not appear in the OpenAI catalogue."""
        provider = OpenAIModelProvider("test-key")

        for model in ("o3", "o3-mini", "o3-pro", "o4-mini", "o3mini", "o3pro", "o4mini"):
            assert provider.validate_model_name(model) is False
            with pytest.raises(ValueError, match="Unsupported OpenAI model"):
                provider.get_capabilities(model)

    def test_get_capabilities_with_alias(self):
        """Test getting model capabilities with alias resolves correctly."""
        provider = OpenAIModelProvider("test-key")

        capabilities = provider.get_capabilities("mini")
        assert capabilities.model_name == "gpt-5.4-mini"
        assert capabilities.friendly_name == "OpenAI (GPT-5.4 Mini)"
        assert capabilities.context_window == 400_000
        assert capabilities.provider == ProviderType.OPENAI

    def test_get_capabilities_gpt5(self):
        """Test getting model capabilities for GPT-5."""
        provider = OpenAIModelProvider("test-key")

        capabilities = provider.get_capabilities("gpt-5")
        assert capabilities.model_name == "gpt-5.5"
        assert capabilities.friendly_name == "OpenAI (GPT-5.5)"
        assert capabilities.context_window == 1_050_000
        assert capabilities.max_output_tokens == 128_000
        assert capabilities.provider == ProviderType.OPENAI
        assert capabilities.supports_extended_thinking is True
        assert capabilities.supports_system_prompts is True
        assert capabilities.supports_streaming is True
        assert capabilities.supports_function_calling is True
        assert capabilities.supports_temperature is False

    def test_get_capabilities_gpt5_mini(self):
        """Test getting model capabilities for GPT-5-mini."""
        provider = OpenAIModelProvider("test-key")

        capabilities = provider.get_capabilities("gpt-5-mini")
        assert capabilities.model_name == "gpt-5.4-mini"
        assert capabilities.friendly_name == "OpenAI (GPT-5.4 Mini)"
        assert capabilities.context_window == 400_000
        assert capabilities.max_output_tokens == 128_000
        assert capabilities.provider == ProviderType.OPENAI
        assert capabilities.supports_extended_thinking is True
        assert capabilities.supports_system_prompts is True
        assert capabilities.supports_streaming is True
        assert capabilities.supports_function_calling is True
        assert capabilities.supports_temperature is False

    def test_get_capabilities_gpt54(self):
        """Test GPT-5.4 capabilities reflect current metadata."""
        provider = OpenAIModelProvider("test-key")

        capabilities = provider.get_capabilities("gpt-5.4")
        assert capabilities.model_name == "gpt-5.4"
        assert capabilities.supports_streaming is True
        assert capabilities.supports_function_calling is True
        assert capabilities.supports_json_mode is True
        assert capabilities.allow_code_generation is True

    def test_removed_gpt52_family_is_not_available(self):
        """Models below GPT-5.4 should not remain in the OpenAI catalogue."""
        provider = OpenAIModelProvider("test-key")

        assert provider.validate_model_name("gpt-5.2") is False
        assert provider.validate_model_name("gpt-5.1-codex") is False
        assert provider.validate_model_name("gpt-5.1-codex-mini") is False

    @patch("providers.openai_compatible.OpenAI")
    def test_generate_content_resolves_alias_before_api_call(self, mock_openai_class):
        """Test that generate_content resolves aliases before making API calls.

        This verifies that aliases like 'mini' get resolved before being sent
        to the OpenAI API.
        """
        # Set up mock OpenAI client
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        # Mock the completion response
        mock_response = MagicMock()
        mock_response.output_text = "Test response"
        mock_response.model = "gpt-5.4-mini"  # API returns the resolved model name
        mock_response.id = "test-id"
        mock_response.created_at = 1234567890
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.total_tokens = 15

        mock_client.responses.create.return_value = mock_response

        provider = OpenAIModelProvider("test-key")

        # Call generate_content with alias 'mini' (resolves to gpt-5.4-mini)
        result = provider.generate_content(
            prompt="Test prompt",
            model_name="mini",
            temperature=1.0,
        )

        # Verify the API was called with the RESOLVED model name
        mock_client.responses.create.assert_called_once()
        call_kwargs = mock_client.responses.create.call_args[1]

        assert call_kwargs["model"] == "gpt-5.4-mini"

        # Reasoning models do not send temperature.
        assert "temperature" not in call_kwargs
        assert len(call_kwargs["input"]) == 1
        assert call_kwargs["input"][0]["role"] == "user"
        assert call_kwargs["input"][0]["content"][0]["text"] == "Test prompt"

        # Verify response
        assert result.content == "Test response"
        assert result.model_name == "gpt-5.4-mini"  # Should be the resolved name

    @patch("providers.openai_compatible.OpenAI")
    def test_generate_content_other_aliases(self, mock_openai_class):
        """Test other alias resolutions in generate_content."""
        # Set up mock
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.output_text = "Test response"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.total_tokens = 15
        mock_client.responses.create.return_value = mock_response

        provider = OpenAIModelProvider("test-key")

        # Test gpt5 -> gpt-5.5
        mock_response.model = "gpt-5.5"
        provider.generate_content(prompt="Test", model_name="gpt5", temperature=1.0)
        call_kwargs = mock_client.responses.create.call_args[1]
        assert call_kwargs["model"] == "gpt-5.5"

        # Test gpt5mini -> gpt-5.4-mini
        mock_response.model = "gpt-5.4-mini"
        provider.generate_content(prompt="Test", model_name="gpt5mini", temperature=1.0)
        call_kwargs = mock_client.responses.create.call_args[1]
        assert call_kwargs["model"] == "gpt-5.4-mini"

    @patch("providers.openai_compatible.OpenAI")
    def test_generate_content_no_alias_passthrough(self, mock_openai_class):
        """Test that full model names pass through unchanged."""
        # Set up mock
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.output_text = "Test response"
        mock_response.model = "gpt-5.4"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.total_tokens = 15
        mock_client.responses.create.return_value = mock_response

        provider = OpenAIModelProvider("test-key")

        provider.generate_content(prompt="Test", model_name="gpt-5.4", temperature=1.0)
        call_kwargs = mock_client.responses.create.call_args[1]
        assert call_kwargs["model"] == "gpt-5.4"  # Should be unchanged

    def test_extended_thinking_capabilities(self):
        """Thinking-mode support should be reflected via ModelCapabilities."""
        provider = OpenAIModelProvider("test-key")

        supported_aliases = [
            "gpt-5.5",
            "gpt-5.4-mini",
            "gpt5",
            "gpt5-mini",
            "gpt5mini",
            "mini",  # resolves to gpt-5-mini
        ]
        for alias in supported_aliases:
            assert provider.get_capabilities(alias).supports_extended_thinking is True

        unsupported_aliases = ["o3", "o3-mini", "o4-mini"]
        for alias in unsupported_aliases:
            assert provider.validate_model_name(alias) is False

        # Invalid models should not validate, treat as unsupported
        assert not provider.validate_model_name("invalid-model")

    @patch("providers.openai_compatible.OpenAI")
    def test_gpt5_routes_to_responses_endpoint(self, mock_openai_class):
        """Test that current GPT reasoning models route to the /v1/responses endpoint."""
        # Set up mock for OpenAI client responses endpoint
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.output_text = "4"
        mock_response.model = "gpt-5.5"
        mock_response.id = "test-id"
        mock_response.created_at = 1234567890
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.total_tokens = 15

        mock_client.responses.create.return_value = mock_response

        provider = OpenAIModelProvider("test-key")

        result = provider.generate_content(prompt="What is 2 + 2?", model_name="gpt-5.5", temperature=1.0)

        # Verify responses.create was called
        mock_client.responses.create.assert_called_once()
        call_args = mock_client.responses.create.call_args[1]
        assert call_args["model"] == "gpt-5.5"
        assert call_args["input"][0]["role"] == "user"
        assert "What is 2 + 2?" in call_args["input"][0]["content"][0]["text"]

        # Verify the response
        assert result.content == "4"
        assert result.model_name == "gpt-5.5"
        assert result.metadata["endpoint"] == "responses"

    @patch("providers.openai_compatible.OpenAI")
    def test_removed_o_series_rejected_before_api_call(self, mock_openai_class):
        """Removed O-series catalogue models are rejected before an API call is made."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        provider = OpenAIModelProvider("test-key")

        with pytest.raises(ValueError, match="Model 'o3-mini' not in allowed models list"):
            provider.generate_content(prompt="Test prompt", model_name="o3-mini", temperature=1.0)

        mock_client.chat.completions.create.assert_not_called()
        mock_client.responses.create.assert_not_called()
