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

        # Token pricing (cost per million tokens in USD)
        self.token_pricing = {
            'opus': {
                'prompt': float(os.getenv('OPUS_PROMPT_COST_PER_MTK', '15.00')),
                'completion': float(os.getenv('OPUS_COMPLETION_COST_PER_MTK', '75.00')),
            },
            'sonnet': {
                'prompt': float(os.getenv('SONNET_PROMPT_COST_PER_MTK', '3.00')),
                'completion': float(os.getenv('SONNET_COMPLETION_COST_PER_MTK', '15.00')),
            },
            'haiku': {
                'prompt': float(os.getenv('HAIKU_PROMPT_COST_PER_MTK', '0.25')),
                'completion': float(os.getenv('HAIKU_COMPLETION_COST_PER_MTK', '1.25')),
            },
        }

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

        Simple family-based matching:
        - Any model containing 'sonnet' -> sonnet mapping
        - Any model containing 'opus' -> opus mapping
        - Any model containing 'haiku' -> haiku mapping
        """
        claude_model_lower = claude_model.lower()

        logger.info(f"Model mapping: input={claude_model}")

        # Check for exact mapping first
        if claude_model in self.model_mapping:
            mapped = self.model_mapping[claude_model]
            logger.info(f"Model mapping (exact): {claude_model} -> {mapped}")
            return mapped

        # Simple family-based matching: look for model family keywords
        # Order matters: check more specific families first

        # Haiku family
        if 'haiku' in claude_model_lower:
            if 'haiku' in self.model_mapping:
                logger.info(f"Model mapping (haiku): {claude_model} -> {self.model_mapping['haiku']}")
                return self.model_mapping['haiku']

        # Opus family
        if 'opus' in claude_model_lower:
            if 'opus' in self.model_mapping:
                logger.info(f"Model mapping (opus): {claude_model} -> {self.model_mapping['opus']}")
                return self.model_mapping['opus']

        # Sonnet family (check last as it's most common)
        if 'sonnet' in claude_model_lower:
            if 'sonnet' in self.model_mapping:
                logger.info(f"Model mapping (sonnet): {claude_model} -> {self.model_mapping['sonnet']}")
                return self.model_mapping['sonnet']

        # Pass through unchanged
        logger.warning(f"No model mapping for {claude_model}, passing through unchanged")
        return claude_model

    def _generate_token(self) -> str:
        """Generate a random access token."""
        return f"cc-launcher-{secrets.token_hex(32)}"

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """
        Calculate cost in USD for a given model and token count.

        Args:
            model: The model name (will be matched to opus/sonnet/haiku)
            input_tokens: Number of input/prompt tokens
            output_tokens: Number of output/completion tokens

        Returns:
            Cost in USD
        """
        model_lower = model.lower()

        # Determine which pricing tier to use
        if 'opus' in model_lower:
            pricing = self.token_pricing['opus']
        elif 'haiku' in model_lower:
            pricing = self.token_pricing['haiku']
        else:
            # Default to sonnet pricing
            pricing = self.token_pricing['sonnet']

        # Cost = (tokens / 1,000,000) * cost_per_million
        prompt_cost = (input_tokens / 1_000_000) * pricing['prompt']
        completion_cost = (output_tokens / 1_000_000) * pricing['completion']

        return prompt_cost + completion_cost

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
            'token_pricing': self.token_pricing,
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
