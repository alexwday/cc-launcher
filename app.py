#!/usr/bin/env python3
"""CC-Launcher - Claude Code Launcher & Proxy Dashboard."""

import os
import sys
import logging
import webbrowser
import threading
from pathlib import Path

from flask import Flask, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Import our modules
from config import Config, setup_ssl
from logger_manager import LoggerManager
from oauth_manager import OAuthManager
from handlers import proxy_bp, dashboard_bp, ProcessManager


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, template_folder='templates')
    CORS(app)

    # Load configuration
    config = Config()

    # Setup SSL (rbc_security if available)
    config.ssl_enabled = setup_ssl()

    # Store config in app context
    app.config['CC_CONFIG'] = config

    # Initialize log manager
    log_manager = LoggerManager()
    app.config['LOG_MANAGER'] = log_manager

    # Initialize OAuth manager if configured
    oauth_manager = None
    if config.is_oauth_configured() and not config.dev_mode:
        try:
            oauth_manager = OAuthManager(
                token_endpoint=config.oauth_token_endpoint,
                client_id=config.oauth_client_id,
                client_secret=config.oauth_client_secret,
                scope=config.oauth_scope,
                refresh_buffer_minutes=config.oauth_refresh_buffer_minutes,
                verify_ssl=config.get_verify_ssl()
            )
            # Attempt initial token fetch
            logger.info("Attempting initial OAuth token fetch...")
            token = oauth_manager.get_token()
            if token:
                logger.info("OAuth token obtained successfully")
            else:
                logger.warning("Failed to obtain OAuth token")
        except Exception as e:
            logger.error(f"OAuth initialization failed: {e}")
            oauth_manager = None

    app.config['OAUTH_MANAGER'] = oauth_manager

    # Initialize process manager
    process_manager = ProcessManager(config.port, config.proxy_access_token)
    app.config['PROCESS_MANAGER'] = process_manager

    # Register blueprints
    app.register_blueprint(proxy_bp)
    app.register_blueprint(dashboard_bp)

    # Dashboard route
    @app.route('/')
    def dashboard():
        return send_from_directory('templates', 'index.html')

    # Log startup info
    log_manager.log_server_event('info', 'CC-Launcher started', {
        'port': config.port,
        'mode': 'placeholder' if config.use_placeholder_mode else 'proxy',
        'target': config.target_endpoint,
        'oauth': config.is_oauth_configured(),
        'ssl': config.ssl_enabled,
    })

    return app


def open_browser(port: int):
    """Open browser after a short delay."""
    import time
    time.sleep(1.5)
    webbrowser.open(f'http://localhost:{port}')


def main():
    """Main entry point."""
    app = create_app()
    config = app.config['CC_CONFIG']

    # Print startup banner
    print()
    print("=" * 60)
    print("  CC-Launcher - Claude Code Launcher & Proxy Dashboard")
    print("=" * 60)
    print()
    print(f"  Dashboard:  http://localhost:{config.port}")
    print(f"  Proxy URL:  http://localhost:{config.port}/v1/messages")
    print()
    print(f"  Target:     {config.target_endpoint}")
    print(f"  Mode:       {'Placeholder' if config.use_placeholder_mode else 'Proxy'}")
    print(f"  SSL:        {'Enabled' if config.ssl_enabled else 'Disabled'}")
    print()
    print("  To use with Claude Code, set these environment variables:")
    print()
    print(f"    export ANTHROPIC_BASE_URL='http://localhost:{config.port}'")
    print(f"    export ANTHROPIC_API_KEY='{config.proxy_access_token}'")
    print()
    print("=" * 60)
    print()

    # Open browser if configured
    if config.auto_open_browser:
        threading.Thread(target=open_browser, args=(config.port,), daemon=True).start()

    # Run the Flask app
    try:
        app.run(
            host='0.0.0.0',
            port=config.port,
            debug=False,
            threaded=True
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
