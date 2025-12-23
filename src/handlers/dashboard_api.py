"""Dashboard API endpoints for cc-launcher."""

import os
import logging
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint('dashboard', __name__)


def get_config():
    """Get config from Flask app context."""
    from flask import current_app
    return current_app.config['CC_CONFIG']


def get_log_manager():
    """Get log manager from Flask app context."""
    from flask import current_app
    return current_app.config['LOG_MANAGER']


def get_process_manager():
    """Get process manager from Flask app context."""
    from flask import current_app
    return current_app.config.get('PROCESS_MANAGER')


@dashboard_bp.route('/api/config', methods=['GET'])
def get_configuration():
    """Get current configuration (with sensitive data redacted)."""
    config = get_config()

    return jsonify({
        'port': config.port,
        'localBaseUrl': f'http://localhost:{config.port}',
        'targetEndpoint': config.target_endpoint,
        'accessToken': config.proxy_access_token,
        'usePlaceholderMode': config.use_placeholder_mode,
        'modelMapping': config.model_mapping,
        'defaultMaxTokens': config.default_max_tokens,
        'oauthConfigured': config.is_oauth_configured(),
        'apiKeyConfigured': config.is_api_key_configured(),
        'devMode': config.dev_mode,
        'sslEnabled': config.ssl_enabled,
    })


@dashboard_bp.route('/api/status', methods=['GET'])
def get_status():
    """Get current system status."""
    config = get_config()
    process_manager = get_process_manager()

    status = {
        'proxy': {
            'running': True,
            'port': config.port,
            'mode': 'placeholder' if config.use_placeholder_mode else 'proxy',
        },
        'claudeCode': {
            'launched': process_manager.is_claude_running() if process_manager else False,
            'workingDirectory': process_manager.working_directory if process_manager else None,
        },
        'authentication': {
            'type': 'oauth' if config.is_oauth_configured() else ('api_key' if config.is_api_key_configured() else 'none'),
            'configured': config.is_oauth_configured() or config.is_api_key_configured() or config.dev_mode,
        }
    }

    return jsonify(status)


@dashboard_bp.route('/api/logs', methods=['GET'])
def get_logs():
    """Get all logs."""
    log_manager = get_log_manager()
    limit = request.args.get('limit', 50, type=int)

    return jsonify({
        'apiCalls': log_manager.get_api_calls(limit),
        'serverEvents': log_manager.get_server_events(limit),
    })


@dashboard_bp.route('/api/logs/api-calls', methods=['GET'])
def get_api_logs():
    """Get API call logs."""
    log_manager = get_log_manager()
    limit = request.args.get('limit', 50, type=int)

    return jsonify(log_manager.get_api_calls(limit))


@dashboard_bp.route('/api/logs/server-events', methods=['GET'])
def get_server_logs():
    """Get server event logs."""
    log_manager = get_log_manager()
    limit = request.args.get('limit', 50, type=int)

    return jsonify(log_manager.get_server_events(limit))


@dashboard_bp.route('/api/logs', methods=['DELETE'])
def clear_logs():
    """Clear all logs."""
    log_manager = get_log_manager()
    log_manager.clear_logs()

    return jsonify({'success': True, 'message': 'Logs cleared'})


@dashboard_bp.route('/api/usage', methods=['GET'])
def get_usage():
    """Get usage statistics."""
    log_manager = get_log_manager()

    return jsonify(log_manager.get_usage_stats())


@dashboard_bp.route('/api/usage/reset', methods=['POST'])
def reset_usage():
    """Reset usage statistics."""
    log_manager = get_log_manager()
    log_manager.reset_usage()

    return jsonify({'success': True, 'message': 'Usage statistics reset'})


@dashboard_bp.route('/api/claude/launch', methods=['POST'])
def launch_claude():
    """Launch Claude Code in external terminal."""
    process_manager = get_process_manager()

    if not process_manager:
        return jsonify({'success': False, 'error': 'Process manager not available'}), 500

    data = request.get_json() or {}
    working_directory = data.get('workingDirectory')

    success, message = process_manager.launch_claude_code(working_directory)

    if success:
        log_manager = get_log_manager()
        log_manager.log_server_event('info', f'Claude Code launched in {working_directory or "home directory"}')
        return jsonify({
            'success': True,
            'message': message,
            'workingDirectory': process_manager.working_directory
        })
    else:
        logger.error(f"Failed to launch Claude Code: {message}")
        return jsonify({'success': False, 'error': message}), 400


@dashboard_bp.route('/api/claude/status', methods=['GET'])
def claude_status():
    """Get Claude Code process status."""
    process_manager = get_process_manager()

    if not process_manager:
        return jsonify({'launched': False, 'workingDirectory': None})

    return jsonify({
        'launched': process_manager.is_claude_running(),
        'workingDirectory': process_manager.working_directory,
    })


@dashboard_bp.route('/api/browse', methods=['GET'])
def browse_directories():
    """Browse directories for working directory selection."""
    path = request.args.get('path', os.path.expanduser('~'))

    # Expand user home directory
    path = os.path.expanduser(path)

    # Security: ensure path exists and is a directory
    if not os.path.exists(path):
        return jsonify({'error': 'Path does not exist', 'path': path}), 404

    if not os.path.isdir(path):
        # If it's a file, return the parent directory
        path = os.path.dirname(path)

    try:
        items = []
        for name in sorted(os.listdir(path)):
            full_path = os.path.join(path, name)
            # Skip hidden files/dirs (starting with .) and system dirs
            if name.startswith('.'):
                continue
            try:
                is_dir = os.path.isdir(full_path)
                items.append({
                    'name': name,
                    'path': full_path,
                    'isDirectory': is_dir,
                })
            except PermissionError:
                continue

        # Sort directories first, then files
        items.sort(key=lambda x: (not x['isDirectory'], x['name'].lower()))

        return jsonify({
            'currentPath': path,
            'parentPath': os.path.dirname(path) if path != '/' else None,
            'items': items,
        })
    except PermissionError:
        return jsonify({'error': 'Permission denied', 'path': path}), 403
    except Exception as e:
        return jsonify({'error': str(e), 'path': path}), 500


@dashboard_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({'status': 'healthy', 'service': 'cc-launcher'})
