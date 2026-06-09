"""Tests for DIAL provider implementation."""

import os
from unittest.mock import MagicMock, patch

import pytest

from providers.dial import DIALModelProvider
from providers.shared import ProviderType


class TestDIALProvider:
    """Test DIAL provider functionality."""

    @patch.dict(os.environ, {"DIAL_API_KEY": "test-key", "DIAL_API_HOST": "https://test.dialx.ai"})
    def test_initialization_with_host(self):
        """Test provider initialization with custom host."""
        provider = DIALModelProvider("test-key")
        assert provider._dial_api_key == "test-key"  # Check internal API key storage
        assert provider.api_key == "placeholder-not-used"  # OpenAI client uses placeholder, auth header removed by hook
        assert provider.base_url == "https://test.dialx.ai/openai"
        assert provider.get_provider_type() == ProviderType.DIAL

    @patch.dict(os.environ, {"DIAL_API_KEY": "test-key", "DIAL_API_HOST": ""}, clear=True)
    def test_initialization_default_host(self):
        """Test provider initialization with default host."""
        provider = DIALModelProvider("test-key")
        assert provider._dial_api_key == "test-key"  # Check internal API key storage
        assert provider.api_key == "placeholder-not-used"  # OpenAI client uses placeholder, auth header removed by hook
        assert provider.base_url == "https://core.dialx.ai/openai"

    def test_initialization_host_normalization(self):
        """Test that host URL is normalized to include /openai suffix."""
        # Test with host missing /openai
        provider = DIALModelProvider("test-key", base_url="https://custom.dialx.ai")
        assert provider.base_url == "https://custom.dialx.ai/openai"

        # Test with host already having /openai
        provider = DIALModelProvider("test-key", base_url="https://custom.dialx.ai/openai")
        assert provider.base_url == "https://custom.dialx.ai/openai"

    @patch.dict(os.environ, {"DIAL_ALLOWED_MODELS": ""}, clear=False)
    @patch("utils.model_restrictions._restriction_service", None)
    def test_model_validation(self):
        """Test model name validation."""
        provider = DIALModelProvider("test-key")

        # O-series deployments are no longer exposed in the default plan catalogue.
        assert provider.validate_model_name("o3-2025-04-16") is False
        assert provider.validate_model_name("o3") is False
        assert provider.validate_model_name("o4-mini-2025-04-16") is False
        assert provider.validate_model_name("o4-mini") is False
        assert provider.validate_model_name("invalid-model") is False

    def test_resolve_model_name(self):
        """Test model name resolution for shorthands."""
        provider = DIALModelProvider("test-key")

        # Removed default models pass through unchanged because no catalogue alias exists.
        assert provider._resolve_model_name("o3") == "o3"
        assert provider._resolve_model_name("o4-mini") == "o4-mini"
        assert provider._resolve_model_name("o3-2025-04-16") == "o3-2025-04-16"
        assert provider._resolve_model_name("o4-mini-2025-04-16") == "o4-mini-2025-04-16"

    @patch.dict(os.environ, {"DIAL_ALLOWED_MODELS": ""}, clear=False)
    @patch("utils.model_restrictions._restriction_service", None)
    def test_get_capabilities(self):
        """Test getting model capabilities."""
        provider = DIALModelProvider("test-key")

        assert provider.MODEL_CAPABILITIES == {}
        with pytest.raises(ValueError, match="Unsupported model 'o3' for provider dial"):
            provider.get_capabilities("o3")

    @patch.dict(os.environ, {"DIAL_ALLOWED_MODELS": ""}, clear=False)
    @patch("utils.model_restrictions._restriction_service", None)
    def test_get_capabilities_invalid_model(self):
        """Test that get_capabilities raises for invalid models."""
        provider = DIALModelProvider("test-key")

        with pytest.raises(ValueError, match="Unsupported model 'invalid-model' for provider dial"):
            provider.get_capabilities("invalid-model")

    @patch("utils.model_restrictions.get_restriction_service")
    def test_get_capabilities_restricted_model(self, mock_get_restriction):
        """Test that get_capabilities respects model restrictions."""
        provider = DIALModelProvider("test-key")

        # Mock restriction service to block the model
        mock_service = MagicMock()
        mock_service.is_allowed.return_value = False
        mock_get_restriction.return_value = mock_service

        with pytest.raises(ValueError, match="Unsupported model 'o3' for provider dial"):
            provider.get_capabilities("o3")

    @patch.dict(os.environ, {"DIAL_ALLOWED_MODELS": ""}, clear=False)
    @patch("utils.model_restrictions._restriction_service", None)
    def test_supports_vision(self):
        """Test vision support detection through model capabilities."""
        provider = DIALModelProvider("test-key")

        with pytest.raises(ValueError):
            provider.get_capabilities("o3")

    @patch("openai.OpenAI")  # Mock the OpenAI class directly from openai module
    def test_generate_content_rejects_removed_default_model(self, mock_openai_class):
        """Removed DIAL catalogue models are rejected before deployment routing."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        provider = DIALModelProvider("test-key")

        with pytest.raises(ValueError, match="Model 'o3' not in allowed models list"):
            provider.generate_content(prompt="Test prompt", model_name="o3", temperature=0.7)

        mock_client.chat.completions.create.assert_not_called()


    def test_provider_type(self):
        """Test provider type identification."""
        provider = DIALModelProvider("test-key")
        assert provider.get_provider_type() == ProviderType.DIAL

    def test_friendly_name(self):
        """Test provider friendly name."""
        provider = DIALModelProvider("test-key")
        assert provider.FRIENDLY_NAME == "DIAL"

    @patch.dict(os.environ, {"DIAL_API_VERSION": "2024-12-01"})
    def test_configurable_api_version(self):
        """Test that API version can be configured via environment variable."""
        provider = DIALModelProvider("test-key")
        # Check that the custom API version is stored
        assert provider.api_version == "2024-12-01"

    def test_default_api_version(self):
        """Test that default API version is used when not configured."""
        # Clear any existing DIAL_API_VERSION from environment
        with patch.dict(os.environ, {}, clear=True):
            # Keep other env vars but ensure DIAL_API_VERSION is not set
            if "DIAL_API_VERSION" in os.environ:
                del os.environ["DIAL_API_VERSION"]

            provider = DIALModelProvider("test-key")
            # Check that the default API version is used
            assert provider.api_version == "2024-12-01-preview"
            # Check that Api-Key header is set
            assert provider.DEFAULT_HEADERS["Api-Key"] == "test-key"

    @patch.dict(os.environ, {"DIAL_ALLOWED_MODELS": "o3-2025-04-16"})
    @patch("utils.model_restrictions._restriction_service", None)
    def test_allowed_models_restriction(self):
        """Allow-list entries cannot re-enable removed default catalogue models."""
        provider = DIALModelProvider("test-key")

        assert provider.validate_model_name("o3-2025-04-16") is False
        assert provider.validate_model_name("o3") is False
        assert provider.validate_model_name("o4-mini-2025-04-16") is False
        assert provider.validate_model_name("o4-mini") is False

    @patch("httpx.Client")
    @patch("openai.OpenAI")
    def test_close_method(self, mock_openai_class, mock_httpx_client_class):
        """Test that the close method properly closes HTTP clients."""
        # Mock the httpx.Client instance that DIALModelProvider will create
        mock_shared_http_client = MagicMock()
        mock_httpx_client_class.return_value = mock_shared_http_client

        # Mock the OpenAI client instances
        mock_openai_client_1 = MagicMock()
        mock_openai_client_2 = MagicMock()
        # Configure side_effect to return different mocks for subsequent calls
        mock_openai_class.side_effect = [mock_openai_client_1, mock_openai_client_2]

        provider = DIALModelProvider("test-key")

        # Mock the superclass's _client attribute directly
        mock_superclass_client = MagicMock()
        provider._client = mock_superclass_client

        # Simulate getting clients for two different deployments to populate _deployment_clients
        provider._get_deployment_client("model_a")
        provider._get_deployment_client("model_b")

        # Now call close
        provider.close()

        # Assert that the shared httpx client's close method was called
        mock_shared_http_client.close.assert_called_once()

        # Assert that the superclass client's close method was called
        mock_superclass_client.close.assert_called_once()

        # Assert that the deployment clients cache is cleared
        assert not provider._deployment_clients
