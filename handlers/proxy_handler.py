"""Anthropic API proxy handler - translates to OpenAI format."""

import time
import logging
import requests
from flask import Blueprint, request, jsonify, Response, stream_with_context, g

from translator import translate_request, translate_response, StreamTranslator
from translator.openai_to_anthropic import translate_error, build_placeholder_response
from translator.streaming import generate_placeholder_stream

logger = logging.getLogger(__name__)

proxy_bp = Blueprint('proxy', __name__)


def get_config():
    """Get config from Flask app context."""
    from flask import current_app
    return current_app.config['CC_CONFIG']


def get_oauth_manager():
    """Get OAuth manager from Flask app context."""
    from flask import current_app
    return current_app.config.get('OAUTH_MANAGER')


def get_log_manager():
    """Get log manager from Flask app context."""
    from flask import current_app
    return current_app.config['LOG_MANAGER']


def verify_api_key():
    """Verify the x-api-key header matches proxy access token."""
    config = get_config()

    # Check x-api-key header (Anthropic style)
    api_key = request.headers.get('x-api-key', '')

    # Also check Authorization header as fallback
    if not api_key:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            api_key = auth_header[7:]

    if not api_key:
        return False, {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'Missing API key'}}

    if api_key != config.proxy_access_token:
        return False, {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'Invalid API key'}}

    return True, None


@proxy_bp.route('/v1/messages', methods=['POST'])
def messages():
    """
    Handle Anthropic /v1/messages requests.

    Translates to OpenAI format, forwards to target endpoint,
    translates response back to Anthropic format.
    """
    start_time = time.time()
    config = get_config()
    log_manager = get_log_manager()

    # Verify API key
    valid, error = verify_api_key()
    if not valid:
        duration_ms = int((time.time() - start_time) * 1000)
        log_manager.log_api_call('POST', '/v1/messages', 401, duration_ms, None, error)
        return jsonify(error), 401

    # Parse request
    try:
        anthropic_request = request.get_json()
    except Exception as e:
        error = {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': f'Invalid JSON: {e}'}}
        duration_ms = int((time.time() - start_time) * 1000)
        log_manager.log_api_call('POST', '/v1/messages', 400, duration_ms, None, error)
        return jsonify(error), 400

    if not anthropic_request:
        error = {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Empty request body'}}
        duration_ms = int((time.time() - start_time) * 1000)
        log_manager.log_api_call('POST', '/v1/messages', 400, duration_ms, None, error)
        return jsonify(error), 400

    # Store original model for response translation
    original_model = anthropic_request.get('model', 'claude-sonnet-4-20250514')
    is_streaming = anthropic_request.get('stream', False)

    # Log incoming request
    num_messages = len(anthropic_request.get('messages', []))
    logger.info(f"-> {original_model} | msgs={num_messages} | stream={is_streaming}")

    # Placeholder mode - return mock response
    if config.use_placeholder_mode:
        if is_streaming:
            return _handle_placeholder_stream(original_model, anthropic_request, start_time, log_manager)
        else:
            response = build_placeholder_response(original_model)
            duration_ms = int((time.time() - start_time) * 1000)
            log_manager.log_api_call('POST', '/v1/messages', 200, duration_ms, anthropic_request, response,
                                    input_tokens=100, output_tokens=20)
            return jsonify(response), 200

    # Translate request to OpenAI format
    try:
        openai_request = translate_request(
            anthropic_request,
            config.map_model_name,
            config.default_max_tokens
        )
    except Exception as e:
        logger.error(f"Translation error: {e}")
        error = {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': f'Translation error: {e}'}}
        duration_ms = int((time.time() - start_time) * 1000)
        log_manager.log_api_call('POST', '/v1/messages', 400, duration_ms, anthropic_request, error)
        return jsonify(error), 400

    # Forward to target endpoint
    target_url = f"{config.target_endpoint}/chat/completions"
    headers = {'Content-Type': 'application/json'}

    # Add authorization
    _add_authorization(headers, config, get_oauth_manager())

    try:
        if is_streaming:
            return _handle_streaming(
                target_url, openai_request, headers, original_model,
                anthropic_request, start_time, config, log_manager
            )
        else:
            return _handle_non_streaming(
                target_url, openai_request, headers, original_model,
                anthropic_request, start_time, config, log_manager
            )
    except requests.exceptions.Timeout:
        error = {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Request timed out'}}
        duration_ms = int((time.time() - start_time) * 1000)
        log_manager.log_api_call('POST', '/v1/messages', 529, duration_ms, anthropic_request, error)
        return jsonify(error), 529
    except requests.exceptions.ConnectionError as e:
        error = {'type': 'error', 'error': {'type': 'api_error', 'message': f'Connection error: {e}'}}
        duration_ms = int((time.time() - start_time) * 1000)
        log_manager.log_api_call('POST', '/v1/messages', 502, duration_ms, anthropic_request, error)
        return jsonify(error), 502
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        error = {'type': 'error', 'error': {'type': 'api_error', 'message': f'Internal error: {e}'}}
        duration_ms = int((time.time() - start_time) * 1000)
        log_manager.log_api_call('POST', '/v1/messages', 500, duration_ms, anthropic_request, error)
        return jsonify(error), 500


def _add_authorization(headers: dict, config, oauth_manager):
    """Add authorization header based on configuration."""
    if config.dev_mode:
        headers['Authorization'] = 'Bearer dev-mock-token'
        logger.debug("Using dev mock token")
        return

    # Priority 1: OAuth
    if oauth_manager:
        try:
            token = oauth_manager.get_token()
            if token:
                headers['Authorization'] = f'Bearer {token}'
                logger.debug("Using OAuth token")
                return
        except Exception as e:
            logger.error(f"Failed to get OAuth token: {e}")

    # Priority 2: Static API key
    if config.is_api_key_configured():
        headers['Authorization'] = f'Bearer {config.target_api_key}'
        logger.debug("Using static API key")
        return

    logger.warning("No authentication configured for target endpoint")


def _handle_non_streaming(target_url, openai_request, headers, original_model,
                          anthropic_request, start_time, config, log_manager):
    """Handle non-streaming request/response."""
    response = requests.post(
        target_url,
        json=openai_request,
        headers=headers,
        timeout=120,
        verify=config.get_verify_ssl()
    )

    duration_ms = int((time.time() - start_time) * 1000)

    if not response.ok:
        try:
            error_data = response.json()
        except:
            error_data = {'error': {'message': response.text or 'Unknown error'}}

        anthropic_error = translate_error(error_data, response.status_code)
        log_manager.log_api_call('POST', '/v1/messages', response.status_code, duration_ms,
                                anthropic_request, anthropic_error)
        return jsonify(anthropic_error), response.status_code

    # Parse and translate response
    try:
        openai_response = response.json()
    except Exception as e:
        error = {'type': 'error', 'error': {'type': 'api_error', 'message': f'Invalid JSON from target: {e}'}}
        log_manager.log_api_call('POST', '/v1/messages', 502, duration_ms, anthropic_request, error)
        return jsonify(error), 502

    anthropic_response = translate_response(openai_response, original_model)

    # Log with token usage
    usage = anthropic_response.get('usage', {})
    log_manager.log_api_call('POST', '/v1/messages', 200, duration_ms, anthropic_request, anthropic_response,
                            input_tokens=usage.get('input_tokens', 0),
                            output_tokens=usage.get('output_tokens', 0))

    logger.info(f"<- stop_reason={anthropic_response.get('stop_reason')} | "
                f"tokens={usage.get('input_tokens', 0)}+{usage.get('output_tokens', 0)}")

    return jsonify(anthropic_response), 200


def _handle_streaming(target_url, openai_request, headers, original_model,
                      anthropic_request, start_time, config, log_manager):
    """Handle streaming request/response."""
    response = requests.post(
        target_url,
        json=openai_request,
        headers=headers,
        timeout=600,
        stream=True,
        verify=config.get_verify_ssl()
    )

    if not response.ok:
        duration_ms = int((time.time() - start_time) * 1000)
        try:
            error_data = response.json()
        except:
            error_data = {'error': {'message': response.text or 'Unknown error'}}

        anthropic_error = translate_error(error_data, response.status_code)
        log_manager.log_api_call('POST', '/v1/messages', response.status_code, duration_ms,
                                anthropic_request, anthropic_error)
        return jsonify(anthropic_error), response.status_code

    # Stream translator
    translator = StreamTranslator(original_model)

    def generate():
        try:
            for chunk in response.iter_lines():
                if chunk:
                    # Translate OpenAI chunk to Anthropic events
                    events = translator.translate_chunk(chunk)
                    for event in events:
                        yield event.encode('utf-8')

            # Log completion
            duration_ms = int((time.time() - start_time) * 1000)
            usage = translator.get_usage()
            log_manager.log_api_call('POST', '/v1/messages', 200, duration_ms,
                                    anthropic_request, {'streaming': True},
                                    input_tokens=usage.get('input_tokens', 0),
                                    output_tokens=usage.get('output_tokens', 0))

            logger.info(f"<- stream complete | tokens={usage.get('input_tokens', 0)}+{usage.get('output_tokens', 0)}")

        except GeneratorExit:
            logger.warning("Client disconnected during stream")
        except Exception as e:
            import json
            import traceback
            logger.error(f"Streaming error: {e}")
            logger.error(traceback.format_exc())
            # Properly escape the error message for JSON
            error_msg = str(e).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
            error_data = {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": error_msg
                }
            }
            error_event = f"event: error\ndata: {json.dumps(error_data)}\n\n"
            yield error_event.encode('utf-8')
        finally:
            response.close()

    return Response(
        stream_with_context(generate()),
        content_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    ), 200


def _handle_placeholder_stream(original_model, anthropic_request, start_time, log_manager):
    """Handle placeholder streaming response."""
    def generate():
        for event in generate_placeholder_stream(original_model):
            yield event.encode('utf-8')

        duration_ms = int((time.time() - start_time) * 1000)
        log_manager.log_api_call('POST', '/v1/messages', 200, duration_ms,
                                anthropic_request, {'streaming': True, 'placeholder': True},
                                input_tokens=100, output_tokens=20)

    return Response(
        stream_with_context(generate()),
        content_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    ), 200
