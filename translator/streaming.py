"""Translate streaming responses between OpenAI and Anthropic SSE formats."""

import json
import logging
import uuid
import time
from typing import Dict, Any, List, Optional, Generator
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class StreamState:
    """Track state during stream translation."""
    message_id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:24]}")
    model: str = 'claude-sonnet-4-20250514'
    message_started: bool = False
    content_block_started: bool = False
    current_block_index: int = 0
    current_block_type: str = 'text'
    accumulated_tool_calls: Dict[int, Dict] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: Optional[str] = None


class StreamTranslator:
    """
    Translates OpenAI streaming chunks to Anthropic SSE events.

    OpenAI format:
        data: {"choices":[{"delta":{"content":"Hi"}}]}

    Anthropic format:
        event: message_start
        data: {"type":"message_start","message":{...}}

        event: content_block_start
        data: {"type":"content_block_start","index":0}

        event: content_block_delta
        data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}

        event: message_stop
        data: {"type":"message_stop"}
    """

    def __init__(self, original_model: str = 'claude-sonnet-4-20250514'):
        self.state = StreamState(model=original_model)

    def translate_chunk(self, openai_chunk: bytes) -> List[str]:
        """
        Translate a single OpenAI SSE chunk to Anthropic SSE events.

        Args:
            openai_chunk: Raw SSE chunk bytes from OpenAI

        Returns:
            List of Anthropic SSE event strings (each includes event: and data: lines)
        """
        events = []

        # Parse the chunk
        try:
            chunk_str = openai_chunk.decode('utf-8').strip()
        except Exception as e:
            logger.error(f"Failed to decode chunk: {e}")
            return events

        # Skip empty lines
        if not chunk_str:
            return events

        # Handle [DONE] marker
        if chunk_str == 'data: [DONE]':
            events.extend(self._emit_stream_end())
            return events

        # Parse data payload
        if not chunk_str.startswith('data: '):
            # Log unexpected chunk format
            logger.debug(f"Unexpected chunk format: {chunk_str[:100]}")
            return events

        try:
            data = json.loads(chunk_str[6:])  # Skip "data: "
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse chunk JSON: {e}, chunk: {chunk_str[:200]}")
            return events

        # Check for error in stream (some APIs send errors via SSE)
        if 'error' in data:
            logger.error(f"Error in stream data: {data['error']}")
            error_obj = data['error']
            if isinstance(error_obj, str):
                error_msg = error_obj
            elif isinstance(error_obj, dict):
                error_msg = error_obj.get('message', str(error_obj))
            else:
                error_msg = str(error_obj)
            error_event = {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": error_msg
                }
            }
            events.append(f"event: error\ndata: {json.dumps(error_event)}\n\n")
            return events

        # Extract delta from choices
        choices = data.get('choices', [])
        if not choices:
            # Check for usage in final chunk
            if 'usage' in data:
                self.state.input_tokens = data['usage'].get('prompt_tokens', 0)
                self.state.output_tokens = data['usage'].get('completion_tokens', 0)
            return events

        choice = choices[0]
        delta = choice.get('delta', {})
        finish_reason = choice.get('finish_reason')

        # Emit message_start if not done yet
        if not self.state.message_started:
            events.append(self._emit_message_start())
            self.state.message_started = True

        # Handle role (first chunk usually)
        if 'role' in delta and not self.state.content_block_started:
            # Role chunk - content block will start with next content
            pass

        # Handle text content
        if 'content' in delta and delta['content']:
            if not self.state.content_block_started:
                events.append(self._emit_content_block_start('text'))
                self.state.content_block_started = True
                self.state.current_block_type = 'text'

            events.append(self._emit_text_delta(delta['content']))

        # Handle tool calls
        if 'tool_calls' in delta:
            for tc_delta in delta['tool_calls']:
                tc_index = tc_delta.get('index', 0)

                # New tool call - initialize but DON'T emit content_block_start yet
                # We need to wait until we have the function name
                if tc_index not in self.state.accumulated_tool_calls:
                    self.state.accumulated_tool_calls[tc_index] = {
                        'id': tc_delta.get('id', f'toolu_{uuid.uuid4().hex[:24]}'),
                        'name': '',
                        'input_json': '',
                        'block_started': False  # Track if we've emitted content_block_start
                    }

                # Accumulate tool call data
                tc_data = self.state.accumulated_tool_calls[tc_index]

                # Update ID if provided
                if 'id' in tc_delta:
                    tc_data['id'] = tc_delta['id']

                if 'function' in tc_delta:
                    if 'name' in tc_delta['function']:
                        tc_data['name'] = tc_delta['function']['name']

                    # Once we have the name, we can emit content_block_start
                    if tc_data['name'] and not tc_data['block_started']:
                        # End previous content block if any
                        if self.state.content_block_started and self.state.current_block_type == 'text':
                            events.append(self._emit_content_block_stop())
                            self.state.current_block_index += 1

                        # Now emit content_block_start with the name
                        events.append(self._emit_content_block_start('tool_use', tc_index))
                        self.state.content_block_started = True
                        self.state.current_block_type = 'tool_use'
                        tc_data['block_started'] = True

                    if 'arguments' in tc_delta['function']:
                        tc_data['input_json'] += tc_delta['function']['arguments']
                        # Only emit delta if block has started
                        if tc_data['block_started']:
                            events.append(self._emit_input_json_delta(tc_delta['function']['arguments']))

        # Handle finish_reason
        if finish_reason:
            self.state.stop_reason = self._translate_finish_reason(finish_reason)

            # End current content block
            if self.state.content_block_started:
                events.append(self._emit_content_block_stop())

        return events

    def _emit_message_start(self) -> str:
        """Emit message_start event."""
        message = {
            'id': self.state.message_id,
            'type': 'message',
            'role': 'assistant',
            'content': [],
            'model': self.state.model,
            'stop_reason': None,
            'stop_sequence': None,
            'usage': {
                'input_tokens': self.state.input_tokens,
                'output_tokens': self.state.output_tokens
            }
        }

        event_data = {
            'type': 'message_start',
            'message': message
        }

        return f"event: message_start\ndata: {json.dumps(event_data)}\n\n"

    def _emit_content_block_start(self, block_type: str, index: Optional[int] = None) -> str:
        """Emit content_block_start event."""
        if index is None:
            index = self.state.current_block_index

        if block_type == 'text':
            content_block = {
                'type': 'text',
                'text': ''
            }
        else:  # tool_use
            tc_data = self.state.accumulated_tool_calls.get(index, {})
            content_block = {
                'type': 'tool_use',
                'id': tc_data.get('id', f'toolu_{uuid.uuid4().hex[:24]}'),
                'name': tc_data.get('name', ''),
                'input': {}
            }

        event_data = {
            'type': 'content_block_start',
            'index': index,
            'content_block': content_block
        }

        return f"event: content_block_start\ndata: {json.dumps(event_data)}\n\n"

    def _emit_text_delta(self, text: str) -> str:
        """Emit content_block_delta event for text."""
        event_data = {
            'type': 'content_block_delta',
            'index': self.state.current_block_index,
            'delta': {
                'type': 'text_delta',
                'text': text
            }
        }

        return f"event: content_block_delta\ndata: {json.dumps(event_data)}\n\n"

    def _emit_input_json_delta(self, json_fragment: str) -> str:
        """Emit content_block_delta event for tool input JSON."""
        # Find the tool call index
        tc_index = self.state.current_block_index

        event_data = {
            'type': 'content_block_delta',
            'index': tc_index,
            'delta': {
                'type': 'input_json_delta',
                'partial_json': json_fragment
            }
        }

        return f"event: content_block_delta\ndata: {json.dumps(event_data)}\n\n"

    def _emit_content_block_stop(self) -> str:
        """Emit content_block_stop event."""
        event_data = {
            'type': 'content_block_stop',
            'index': self.state.current_block_index
        }

        return f"event: content_block_stop\ndata: {json.dumps(event_data)}\n\n"

    def _emit_stream_end(self) -> List[str]:
        """Emit final events to close the stream."""
        events = []

        # message_delta with final usage and stop_reason
        event_data = {
            'type': 'message_delta',
            'delta': {
                'stop_reason': self.state.stop_reason or 'end_turn',
                'stop_sequence': None
            },
            'usage': {
                'output_tokens': self.state.output_tokens
            }
        }
        events.append(f"event: message_delta\ndata: {json.dumps(event_data)}\n\n")

        # message_stop
        events.append(f"event: message_stop\ndata: {{\"type\":\"message_stop\"}}\n\n")

        return events

    def _translate_finish_reason(self, finish_reason: str) -> str:
        """Translate OpenAI finish_reason to Anthropic stop_reason."""
        mapping = {
            'stop': 'end_turn',
            'length': 'max_tokens',
            'tool_calls': 'tool_use',
            'content_filter': 'end_turn',
            'function_call': 'tool_use',
        }
        return mapping.get(finish_reason, 'end_turn')

    def get_usage(self) -> Dict[str, int]:
        """Get accumulated token usage."""
        return {
            'input_tokens': self.state.input_tokens,
            'output_tokens': self.state.output_tokens
        }


def generate_placeholder_stream(
    model: str = 'claude-sonnet-4-20250514',
    content: str = 'This is a placeholder streaming response from cc-launcher.'
) -> Generator[str, None, None]:
    """Generate a placeholder Anthropic streaming response."""
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    # message_start
    message = {
        'id': msg_id,
        'type': 'message',
        'role': 'assistant',
        'content': [],
        'model': model,
        'stop_reason': None,
        'stop_sequence': None,
        'usage': {'input_tokens': 100, 'output_tokens': 0}
    }
    yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': message})}\n\n"

    # content_block_start
    yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"

    # content_block_delta - stream word by word
    words = content.split()
    for i, word in enumerate(words):
        text = word + (' ' if i < len(words) - 1 else '')
        delta_data = {
            'type': 'content_block_delta',
            'index': 0,
            'delta': {'type': 'text_delta', 'text': text}
        }
        yield f"event: content_block_delta\ndata: {json.dumps(delta_data)}\n\n"
        time.sleep(0.05)  # Simulate streaming delay

    # content_block_stop
    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"

    # message_delta
    yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': len(words)}})}\n\n"

    # message_stop
    yield f"event: message_stop\ndata: {{\"type\":\"message_stop\"}}\n\n"
