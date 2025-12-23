"""Configuration management for cc-launcher."""

import os
import secrets
import logging

logger = logging.getLogger(__name__)


class Config:
    """Application configuration loaded from environment variables."""

    def __init__(self):
        # Proxy settings
        self.port = int(os.getenv('PROXY_PORT', '5000'))
        self.proxy_access_token = os.getenv('PROXY_ACCESS_TOKEN') or self._generate_token()

        # Target endpoint
        self.target_endpoint = os.getenv('TARGET_ENDPOINT', 'https://api.openai.com/v1')
        # Check TARGET_API_KEY, fall back to OPENAI_API_KEY
        self.target_api_key = os.getenv('TARGET_API_KEY') or os.getenv('OPENAI_API_KEY')
        self.use_placeholder_mode = os.getenv('USE_PLACEHOLDER_MODE', 'false').lower() == 'true'

        # Model configuration
        self.model_mapping = self._parse_model_mapping(os.getenv('MODEL_MAPPING', ''))
        self.default_max_tokens = int(os.getenv('DEFAULT_MAX_TOKENS', '16384'))

        # OAuth settings
        self.oauth_token_endpoint = os.getenv('OAUTH_TOKEN_ENDPOINT')
        self.oauth_client_id = os.getenv('OAUTH_CLIENT_ID')
        self.oauth_client_secret = os.getenv('OAUTH_CLIENT_SECRET')
        self.oauth_scope = os.getenv('OAUTH_SCOPE')
        self.oauth_refresh_buffer_minutes = int(os.getenv('OAUTH_REFRESH_BUFFER_MINUTES', '5'))

        # Behavior
        self.dev_mode = os.getenv('DEV_MODE', 'false').lower() == 'true'
        self.skip_ssl_verify = os.getenv('SKIP_SSL_VERIFY', 'false').lower() == 'true'
        self.auto_open_browser = os.getenv('AUTO_OPEN_BROWSER', 'true').lower() == 'true'

        # SSL verification state (set by setup_ssl)
        self.ssl_enabled = True

    def _parse_model_mapping(self, mapping_str: str) -> dict:
        """Parse model mapping from environment (format: source=target,source2=target2)."""
        mapping = {}
        if not mapping_str:
            return mapping

        for pair in mapping_str.split(','):
            if '=' in pair:
                source, target = pair.split('=', 1)
                mapping[source.strip()] = target.strip()

        return mapping

    def map_model_name(self, claude_model: str) -> str:
        """
        Map Claude model name to target model name.

        Supports flexible matching for dated model names like 'claude-sonnet-4-5-20250929'.
        Normalizes dots/dashes for comparison (4.5 matches 4-5).
        """
        claude_model_lower = claude_model.lower()
        # Normalize: treat dots and dashes as equivalent for matching
        claude_model_normalized = claude_model_lower.replace('.', '-')

        logger.info(f"Model mapping: input={claude_model}")

        # Check for exact mapping first
        if claude_model in self.model_mapping:
            mapped = self.model_mapping[claude_model]
            logger.info(f"Model mapping (exact): {claude_model} -> {mapped}")
            return mapped

        # Check for partial matches in the mapping keys (normalize both sides)
        for source, target in self.model_mapping.items():
            source_normalized = source.lower().replace('.', '-')
            if source_normalized in claude_model_normalized or claude_model_normalized in source_normalized:
                logger.info(f"Model mapping (partial): {claude_model} -> {target}")
                return target

        # Fallback: Pattern-based matching for common Claude model families
        # Sonnet 4.5 / 4-5 (the latest Sonnet)
        if 'sonnet-4-5' in claude_model_normalized or 'sonnet-4.5' in claude_model_lower:
            for source, target in self.model_mapping.items():
                source_norm = source.lower().replace('.', '-')
                if 'sonnet' in source_norm and '4-5' in source_norm:
                    logger.info(f"Model mapping (fallback sonnet-4.5): {claude_model} -> {target}")
                    return target

        # Opus 4 / 4.1 (the latest Opus)
        if 'opus-4' in claude_model_normalized or 'opus4' in claude_model_lower:
            for source, target in self.model_mapping.items():
                if 'opus' in source.lower():
                    logger.info(f"Model mapping (fallback opus): {claude_model} -> {target}")
                    return target

        # Sonnet 4 (not 4.5)
        if 'sonnet-4' in claude_model_normalized and 'sonnet-4-5' not in claude_model_normalized:
            for source, target in self.model_mapping.items():
                source_norm = source.lower().replace('.', '-')
                if 'sonnet' in source_norm and '4-5' not in source_norm and '4' in source_norm:
                    logger.info(f"Model mapping (fallback sonnet-4): {claude_model} -> {target}")
                    return target

        # Haiku
        if 'haiku' in claude_model_lower:
            for source, target in self.model_mapping.items():
                if 'haiku' in source.lower():
                    logger.info(f"Model mapping (fallback haiku): {claude_model} -> {target}")
                    return target

        # Pass through unchanged
        logger.warning(f"No model mapping for {claude_model}, passing through unchanged")
        return claude_model

    def _generate_token(self) -> str:
        """Generate a random access token."""
        return f"cc-launcher-{secrets.token_hex(32)}"

    def is_oauth_configured(self) -> bool:
        """Check if OAuth is configured."""
        return bool(
            self.oauth_token_endpoint and
            self.oauth_client_id and
            self.oauth_client_secret
        )

    def is_api_key_configured(self) -> bool:
        """Check if direct API key is configured."""
        return bool(self.target_api_key)

    def get_verify_ssl(self) -> bool:
        """Get SSL verification setting."""
        if self.skip_ssl_verify:
            return False
        return self.ssl_enabled

    def to_dict(self) -> dict:
        """Return configuration as dictionary (for API response)."""
        return {
            'port': self.port,
            'target_endpoint': self.target_endpoint,
            'use_placeholder_mode': self.use_placeholder_mode,
            'model_mapping': self.model_mapping,
            'default_max_tokens': self.default_max_tokens,
            'oauth_configured': self.is_oauth_configured(),
            'api_key_configured': self.is_api_key_configured(),
            'dev_mode': self.dev_mode,
            'ssl_enabled': self.ssl_enabled,
        }


def setup_ssl() -> bool:
    """
    Setup SSL/RBC Security with graceful fallback.

    Returns True if SSL verification should be enabled, False otherwise.
    """
    try:
        import rbc_security
        rbc_security.enable_certs()
        logger.info("RBC Security enabled - SSL verification active")
        return True
    except ImportError:
        logger.warning("rbc_security not available - SSL verification disabled")
        return False
    except Exception as e:
        logger.warning(f"rbc_security setup failed: {e} - SSL verification disabled")
        return False
