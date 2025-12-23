"""Translate OpenAI API responses to Anthropic format."""

import logging
import uuid
import time
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


def translate_response(
    openai_response: Dict[str, Any],
    original_model: str = 'claude-sonnet-4-20250514'
) -> Dict[str, Any]:
    """
    Translate an OpenAI /v1/chat/completions response to Anthropic /v1/messages format.

    Args:
        openai_response: The OpenAI API response body
        original_model: The original Claude model name from the request

    Returns:
        Anthropic-compatible response body
    """
    # Generate Anthropic-style message ID
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    anthropic_response = {
        'id': msg_id,
        'type': 'message',
        'role': 'assistant',
        'model': original_model,
        'content': [],
        'stop_reason': None,
        'stop_sequence': None,
        'usage': {
            'input_tokens': 0,
            'output_tokens': 0
        }
    }

    # Extract from OpenAI response
    choices = openai_response.get('choices', [])
    if not choices:
        logger.warning("OpenAI response has no choices")
        return anthropic_response

    choice = choices[0]
    message = choice.get('message', {})
    finish_reason = choice.get('finish_reason')

    # Translate content
    content_blocks = []

    # Text content
    text_content = message.get('content')
    if text_content:
        content_blocks.append({
            'type': 'text',
            'text': text_content
        })

    # Tool calls -> tool_use blocks
    tool_calls = message.get('tool_calls', [])
    for tc in tool_calls:
        if tc.get('type') == 'function':
            func = tc.get('function', {})
            # Parse arguments JSON
            import json
            try:
                args = json.loads(func.get('arguments', '{}'))
            except json.JSONDecodeError:
                args = {'raw': func.get('arguments', '')}

            content_blocks.append({
                'type': 'tool_use',
                'id': tc.get('id', f'toolu_{uuid.uuid4().hex[:24]}'),
                'name': func.get('name', ''),
                'input': args
            })

    anthropic_response['content'] = content_blocks

    # Translate finish_reason to stop_reason
    anthropic_response['stop_reason'] = _translate_finish_reason(finish_reason)

    # Usage
    usage = openai_response.get('usage', {})
    anthropic_response['usage'] = {
        'input_tokens': usage.get('prompt_tokens', 0),
        'output_tokens': usage.get('completion_tokens', 0)
    }

    return anthropic_response


def translate_error(
    error_response: Dict[str, Any],
    status_code: int = 500
) -> Dict[str, Any]:
    """
    Translate an error response to Anthropic format.

    Handles both OpenAI and Anthropic error formats (some backends return Anthropic errors).

    Args:
        error_response: The error response (could be OpenAI or Anthropic format)
        status_code: HTTP status code

    Returns:
        Anthropic-compatible error response
    """
    logger.debug(f"Translating error response: {error_response}")

    # Check if already in Anthropic format
    if error_response.get('type') == 'error' and 'error' in error_response:
        # Already Anthropic format, return as-is
        return error_response

    # Extract error info - could be nested in 'error' key (OpenAI) or at top level
    error_info = error_response.get('error', {})
    if isinstance(error_info, str):
        # Some APIs return error as a string
        return {
            'type': 'error',
            'error': {
                'type': 'api_error',
                'message': error_info
            }
        }

    # Map OpenAI error types to Anthropic types
    error_type_map = {
        'invalid_request_error': 'invalid_request_error',
        'authentication_error': 'authentication_error',
        'permission_error': 'permission_error',
        'not_found_error': 'not_found_error',
        'rate_limit_error': 'rate_limit_error',
        'server_error': 'api_error',
        'timeout': 'overloaded_error',
    }

    # Try to get error type from various places
    openai_type = (
        error_info.get('type') or
        error_info.get('code') or
        error_response.get('type') or
        'api_error'
    )
    anthropic_type = error_type_map.get(openai_type, 'api_error')

    # Try to get error message from various places
    error_message = (
        error_info.get('message') or
        error_response.get('message') or
        error_response.get('detail') or
        str(error_response) if not error_info else 'An error occurred'
    )

    return {
        'type': 'error',
        'error': {
            'type': anthropic_type,
            'message': error_message
        }
    }


def _translate_finish_reason(finish_reason: Optional[str]) -> Optional[str]:
    """Translate OpenAI finish_reason to Anthropic stop_reason."""
    if not finish_reason:
        return None

    mapping = {
        'stop': 'end_turn',
        'length': 'max_tokens',
        'tool_calls': 'tool_use',
        'content_filter': 'end_turn',  # No direct equivalent
        'function_call': 'tool_use',  # Legacy function calling
    }

    return mapping.get(finish_reason, 'end_turn')


def build_placeholder_response(
    model: str = 'claude-sonnet-4-20250514',
    content: str = 'This is a placeholder response from cc-launcher.'
) -> Dict[str, Any]:
    """Build a placeholder Anthropic response for testing."""
    return {
        'id': f"msg_{uuid.uuid4().hex[:24]}",
        'type': 'message',
        'role': 'assistant',
        'model': model,
        'content': [
            {
                'type': 'text',
                'text': content
            }
        ],
        'stop_reason': 'end_turn',
        'stop_sequence': None,
        'usage': {
            'input_tokens': 100,
            'output_tokens': 20
        }
    }
