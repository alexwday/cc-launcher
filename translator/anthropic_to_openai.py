"""Translate Anthropic API requests to OpenAI format."""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


def translate_request(
    anthropic_request: Dict[str, Any],
    model_mapper: callable,
    default_max_tokens: int = 16384
) -> Dict[str, Any]:
    """
    Translate an Anthropic /v1/messages request to OpenAI /v1/chat/completions format.

    Args:
        anthropic_request: The Anthropic API request body
        model_mapper: Function to map Claude model names to target model names
        default_max_tokens: Default max_tokens if not specified

    Returns:
        OpenAI-compatible request body
    """
    openai_request = {}

    # Model mapping
    claude_model = anthropic_request.get('model', 'claude-sonnet-4-20250514')
    openai_request['model'] = model_mapper(claude_model)
    logger.debug(f"Model: {claude_model} -> {openai_request['model']}")

    # Build messages array
    messages = []

    # System prompt (Anthropic has it at top level, OpenAI has it as first message)
    system_prompt = anthropic_request.get('system')
    if system_prompt:
        if isinstance(system_prompt, str):
            messages.append({'role': 'system', 'content': system_prompt})
        elif isinstance(system_prompt, list):
            # Anthropic supports array of content blocks for system
            system_text = ' '.join(
                block.get('text', '') for block in system_prompt
                if block.get('type') == 'text'
            )
            if system_text:
                messages.append({'role': 'system', 'content': system_text})

    # Translate messages
    for msg in anthropic_request.get('messages', []):
        translated = _translate_message(msg)
        if translated:
            # _translate_message can return a list (e.g., user message with embedded tool_results)
            if isinstance(translated, list):
                messages.extend(translated)
            else:
                messages.append(translated)

    openai_request['messages'] = messages

    # max_tokens (Anthropic requires it, OpenAI makes it optional - but we need it for RBC)
    max_tokens = anthropic_request.get('max_tokens', default_max_tokens)
    openai_request['max_tokens'] = max_tokens
    if 'max_tokens' not in anthropic_request:
        logger.info(f"Injected max_tokens={default_max_tokens} (not in original request)")

    # Temperature
    if 'temperature' in anthropic_request:
        openai_request['temperature'] = anthropic_request['temperature']

    # Top P
    if 'top_p' in anthropic_request:
        openai_request['top_p'] = anthropic_request['top_p']

    # Stop sequences
    if 'stop_sequences' in anthropic_request:
        openai_request['stop'] = anthropic_request['stop_sequences']

    # Streaming
    if 'stream' in anthropic_request:
        openai_request['stream'] = anthropic_request['stream']
        # Request usage in stream for token counting
        if anthropic_request['stream']:
            openai_request['stream_options'] = {'include_usage': True}

    # Tools/Functions
    if 'tools' in anthropic_request:
        openai_request['tools'] = _translate_tools(anthropic_request['tools'])

    # Tool choice
    if 'tool_choice' in anthropic_request:
        openai_request['tool_choice'] = _translate_tool_choice(anthropic_request['tool_choice'])

    return openai_request


def _translate_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Translate a single message from Anthropic to OpenAI format."""
    role = msg.get('role')
    content = msg.get('content')

    if role == 'user':
        return _translate_user_message(msg)
    elif role == 'assistant':
        return _translate_assistant_message(msg)
    elif role == 'tool_result':
        # Anthropic tool_result -> OpenAI tool role
        return _translate_tool_result(msg)

    logger.warning(f"Unknown message role: {role}")
    return None


def _translate_user_message(msg: Dict[str, Any]) -> Any:
    """
    Translate a user message.

    Returns either a single message dict or a list of messages
    (when tool_results are embedded and need to be extracted as separate tool messages).
    """
    content = msg.get('content')

    # Simple string content
    if isinstance(content, str):
        return {'role': 'user', 'content': content}

    # Content blocks array
    if isinstance(content, list):
        # Separate tool_results from other content
        tool_results = []
        other_content = []

        for block in content:
            if not isinstance(block, dict):
                continue

            if block.get('type') == 'tool_result':
                # Extract tool_result as separate OpenAI tool message
                tool_use_id = block.get('tool_use_id', '')
                tool_content = block.get('content', '')
                is_error = block.get('is_error', False)

                # Content can be string or array of content blocks
                if isinstance(tool_content, list):
                    tool_content = ' '.join(
                        b.get('text', '') for b in tool_content
                        if isinstance(b, dict) and b.get('type') == 'text'
                    )

                tool_results.append({
                    'role': 'tool',
                    'tool_call_id': tool_use_id,
                    'content': str(tool_content) if not is_error else f"Error: {tool_content}"
                })
            elif block.get('type') == 'text':
                other_content.append({
                    'type': 'text',
                    'text': block.get('text', '')
                })
            elif block.get('type') == 'image':
                # Anthropic image format
                source = block.get('source', {})
                if source.get('type') == 'base64':
                    other_content.append({
                        'type': 'image_url',
                        'image_url': {
                            'url': f"data:{source.get('media_type', 'image/png')};base64,{source.get('data', '')}"
                        }
                    })

        # Build result: tool messages first (they respond to previous assistant's tool_calls),
        # then user message with remaining content
        result = []

        # Add tool result messages first
        result.extend(tool_results)

        # Add user message if there's non-tool content
        if other_content:
            if len(other_content) == 1 and other_content[0].get('type') == 'text':
                # Simple text content
                result.append({'role': 'user', 'content': other_content[0].get('text', '')})
            else:
                # Mixed content array
                result.append({'role': 'user', 'content': other_content})

        # Return single message or list
        if len(result) == 1:
            return result[0]
        elif len(result) > 1:
            return result
        else:
            return {'role': 'user', 'content': ''}

    return {'role': 'user', 'content': str(content)}


def _translate_assistant_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Translate an assistant message."""
    content = msg.get('content')
    result = {'role': 'assistant'}

    # Simple string content
    if isinstance(content, str):
        result['content'] = content
        return result

    # Content blocks array
    if isinstance(content, list):
        text_parts = []
        tool_calls = []

        for i, block in enumerate(content):
            if not isinstance(block, dict):
                continue

            if block.get('type') == 'text':
                text_parts.append(block.get('text', ''))
            elif block.get('type') == 'tool_use':
                # Anthropic tool_use -> OpenAI tool_calls
                import json
                tool_calls.append({
                    'id': block.get('id', f'call_{i}'),
                    'type': 'function',
                    'function': {
                        'name': block.get('name', ''),
                        'arguments': json.dumps(block.get('input', {}))
                    }
                })

        if text_parts:
            result['content'] = ' '.join(text_parts)
        else:
            result['content'] = None

        if tool_calls:
            result['tool_calls'] = tool_calls

        return result

    result['content'] = str(content) if content else None
    return result


def _translate_tool_result(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a tool_result message to OpenAI tool message."""
    tool_use_id = msg.get('tool_use_id', '')
    content = msg.get('content', '')

    # Content can be string or content blocks
    if isinstance(content, list):
        content = ' '.join(
            block.get('text', '') for block in content
            if isinstance(block, dict) and block.get('type') == 'text'
        )

    return {
        'role': 'tool',
        'tool_call_id': tool_use_id,
        'content': str(content)
    }


def _translate_tools(anthropic_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Translate Anthropic tools to OpenAI functions format."""
    openai_tools = []

    for tool in anthropic_tools:
        openai_tool = {
            'type': 'function',
            'function': {
                'name': tool.get('name', ''),
                'description': tool.get('description', ''),
                'parameters': tool.get('input_schema', {'type': 'object', 'properties': {}})
            }
        }
        openai_tools.append(openai_tool)

    return openai_tools


def _translate_tool_choice(anthropic_choice: Any) -> Any:
    """Translate Anthropic tool_choice to OpenAI format."""
    if anthropic_choice is None:
        return None

    if isinstance(anthropic_choice, str):
        # Direct string values
        if anthropic_choice == 'auto':
            return 'auto'
        elif anthropic_choice == 'any':
            return 'required'
        elif anthropic_choice == 'none':
            return 'none'

    if isinstance(anthropic_choice, dict):
        choice_type = anthropic_choice.get('type')

        if choice_type == 'auto':
            return 'auto'
        elif choice_type == 'any':
            return 'required'
        elif choice_type == 'none':
            return 'none'
        elif choice_type == 'tool':
            # Specific tool
            return {
                'type': 'function',
                'function': {
                    'name': anthropic_choice.get('name', '')
                }
            }

    return 'auto'
