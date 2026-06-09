"""Test the MODEL_CAPABILITIES aliases structure across all providers."""

from providers.dial import DIALModelProvider
from providers.gemini import GeminiModelProvider
from providers.openai import OpenAIModelProvider
from providers.xai import XAIModelProvider


class TestSupportedModelsAliases:
    """Test that all providers have correctly structured MODEL_CAPABILITIES with aliases."""

    def test_gemini_provider_aliases(self):
        """Test Gemini provider's alias structure."""
        provider = GeminiModelProvider("test-key")

        # Check that all models have ModelCapabilities with aliases
        for model_name, config in provider.MODEL_CAPABILITIES.items():
            assert hasattr(config, "aliases"), f"{model_name} must have aliases attribute"
            assert isinstance(config.aliases, list), f"{model_name} aliases must be a list"

        # Test specific aliases
        assert "flash" in provider.MODEL_CAPABILITIES["gemini-3.5-flash"].aliases
        assert "pro" in provider.MODEL_CAPABILITIES["gemini-3.1-pro-preview"].aliases
        assert "flashlite" in provider.MODEL_CAPABILITIES["gemini-3.1-flash-lite"].aliases
        assert "flash-lite" in provider.MODEL_CAPABILITIES["gemini-3.1-flash-lite"].aliases

        # Test alias resolution
        assert provider._resolve_model_name("flash") == "gemini-3.5-flash"
        assert provider._resolve_model_name("pro") == "gemini-3.1-pro-preview"
        assert provider._resolve_model_name("flashlite") == "gemini-3.1-flash-lite"

        # Test case insensitive resolution
        assert provider._resolve_model_name("Flash") == "gemini-3.5-flash"
        assert provider._resolve_model_name("PRO") == "gemini-3.1-pro-preview"

    def test_openai_provider_aliases(self):
        """Test OpenAI provider's alias structure."""
        provider = OpenAIModelProvider("test-key")

        # Check that all models have ModelCapabilities with aliases
        for model_name, config in provider.MODEL_CAPABILITIES.items():
            assert hasattr(config, "aliases"), f"{model_name} must have aliases attribute"
            assert isinstance(config.aliases, list), f"{model_name} aliases must be a list"

        # Test specific aliases
        assert "mini" in provider.MODEL_CAPABILITIES["gpt-5.4-mini"].aliases
        assert "gpt5.4" in provider.MODEL_CAPABILITIES["gpt-5.4"].aliases
        assert "gpt5.4mini" in provider.MODEL_CAPABILITIES["gpt-5.4-mini"].aliases
        assert "o3" not in provider.MODEL_CAPABILITIES
        assert "o3-mini" not in provider.MODEL_CAPABILITIES
        assert "o3-pro" not in provider.MODEL_CAPABILITIES
        assert "o4-mini" not in provider.MODEL_CAPABILITIES

        # Test alias resolution
        assert provider._resolve_model_name("mini") == "gpt-5.4-mini"
        assert provider._resolve_model_name("gpt5.4") == "gpt-5.4"
        assert provider._resolve_model_name("gpt5.4mini") == "gpt-5.4-mini"
        assert provider._resolve_model_name("o3mini") == "o3mini"
        assert provider._resolve_model_name("o3pro") == "o3pro"
        assert provider._resolve_model_name("o4mini") == "o4mini"

        # Test case insensitive resolution
        assert provider._resolve_model_name("Mini") == "gpt-5.4-mini"
        assert provider._resolve_model_name("Gpt5.4") == "gpt-5.4"
        assert provider._resolve_model_name("O3MINI") == "O3MINI"

    def test_xai_provider_aliases(self):
        """Test XAI provider's alias structure."""
        provider = XAIModelProvider("test-key")

        # Check that all models have ModelCapabilities with aliases
        for model_name, config in provider.MODEL_CAPABILITIES.items():
            assert hasattr(config, "aliases"), f"{model_name} must have aliases attribute"
            assert isinstance(config.aliases, list), f"{model_name} aliases must be a list"

        # Test specific aliases
        assert "grok" in provider.MODEL_CAPABILITIES["grok-4.3"].aliases
        assert "grok4" in provider.MODEL_CAPABILITIES["grok-4.3"].aliases
        assert "build" in provider.MODEL_CAPABILITIES["grok-build-0.1"].aliases

        # Test alias resolution
        assert provider._resolve_model_name("grok") == "grok-4.3"
        assert provider._resolve_model_name("grok4") == "grok-4.3"
        assert provider._resolve_model_name("grok-4.1-fast-reasoning") == "grok-4.3"
        assert provider._resolve_model_name("build") == "grok-build-0.1"

        # Test case insensitive resolution
        assert provider._resolve_model_name("Grok") == "grok-4.3"
        assert provider._resolve_model_name("GROK-4.1-FAST-REASONING") == "grok-4.3"

    def test_dial_provider_aliases(self):
        """Test DIAL provider's alias structure."""
        provider = DIALModelProvider("test-key")

        # Check that all models have ModelCapabilities with aliases
        for model_name, config in provider.MODEL_CAPABILITIES.items():
            assert hasattr(config, "aliases"), f"{model_name} must have aliases attribute"
            assert isinstance(config.aliases, list), f"{model_name} aliases must be a list"

        # DIAL no longer exposes default O-series plan models.
        assert provider.MODEL_CAPABILITIES == {}

        # Test alias resolution
        assert provider._resolve_model_name("o3") == "o3"
        assert provider._resolve_model_name("o4-mini") == "o4-mini"

        # Test case insensitive resolution
        assert provider._resolve_model_name("O3") == "O3"

    def test_list_models_includes_aliases(self):
        """Test that list_models returns both base models and aliases."""
        # Test Gemini
        gemini_provider = GeminiModelProvider("test-key")
        gemini_models = gemini_provider.list_models(respect_restrictions=False)
        assert "gemini-2.5-flash" in gemini_models
        assert "flash" in gemini_models
        assert "gemini-3.1-pro-preview" in gemini_models
        assert "pro" in gemini_models

        # Test OpenAI
        openai_provider = OpenAIModelProvider("test-key")
        openai_models = openai_provider.list_models(respect_restrictions=False)
        assert "mini" in openai_models
        assert "gpt-5.4-mini" in openai_models
        assert "o4-mini" not in openai_models
        assert "o3-mini" not in openai_models
        assert "o3mini" not in openai_models

        # Test XAI
        xai_provider = XAIModelProvider("test-key")
        xai_models = xai_provider.list_models(respect_restrictions=False)
        assert "grok-4.3" in xai_models
        assert "grok" in xai_models
        assert "grok-build-0.1" in xai_models

        # Test DIAL
        dial_provider = DIALModelProvider("test-key")
        dial_models = dial_provider.list_models(respect_restrictions=False)
        assert dial_models == []

    def test_list_models_all_known_variant_includes_aliases(self):
        """Unified list_models should support lowercase, alias-inclusive listings."""
        # Test Gemini
        gemini_provider = GeminiModelProvider("test-key")
        gemini_all = gemini_provider.list_models(
            respect_restrictions=False,
            include_aliases=True,
            lowercase=True,
            unique=True,
        )
        assert "gemini-2.5-flash" in gemini_all
        assert "flash" in gemini_all
        assert "gemini-3.1-pro-preview" in gemini_all
        assert "pro" in gemini_all
        # All should be lowercase
        assert all(model == model.lower() for model in gemini_all)

        # Test OpenAI
        openai_provider = OpenAIModelProvider("test-key")
        openai_all = openai_provider.list_models(
            respect_restrictions=False,
            include_aliases=True,
            lowercase=True,
            unique=True,
        )
        assert "mini" in openai_all
        assert "gpt-5.4-mini" in openai_all
        assert "o4-mini" not in openai_all
        assert "o3-mini" not in openai_all
        assert "o3mini" not in openai_all
        # All should be lowercase
        assert all(model == model.lower() for model in openai_all)

    def test_no_string_shorthand_in_supported_models(self):
        """Test that no provider has string-based shorthands anymore."""
        providers = [
            GeminiModelProvider("test-key"),
            OpenAIModelProvider("test-key"),
            XAIModelProvider("test-key"),
            DIALModelProvider("test-key"),
        ]

        for provider in providers:
            for model_name, config in provider.MODEL_CAPABILITIES.items():
                # All values must be ModelCapabilities objects, not strings or dicts
                from providers.shared import ModelCapabilities

                assert isinstance(config, ModelCapabilities), (
                    f"{provider.__class__.__name__}.MODEL_CAPABILITIES['{model_name}'] "
                    f"must be a ModelCapabilities object, not {type(config).__name__}"
                )

    def test_resolve_returns_original_if_not_found(self):
        """Test that _resolve_model_name returns original name if alias not found."""
        providers = [
            GeminiModelProvider("test-key"),
            OpenAIModelProvider("test-key"),
            XAIModelProvider("test-key"),
            DIALModelProvider("test-key"),
        ]

        for provider in providers:
            # Test with unknown model name
            assert provider._resolve_model_name("unknown-model") == "unknown-model"
            assert provider._resolve_model_name("gpt-4") == "gpt-4"
            assert provider._resolve_model_name("claude-3") == "claude-3"
