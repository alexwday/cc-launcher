"""Process management for launching Claude Code."""

import os
import sys
import subprocess
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ProcessManager:
    """Manages Claude Code process launching."""

    def __init__(self, proxy_port: int, proxy_token: str):
        self.proxy_port = proxy_port
        self.proxy_token = proxy_token
        self.working_directory: Optional[str] = None
        self._launched = False

    def launch_claude_code(self, working_directory: Optional[str] = None):
        """
        Launch Claude Code in an external terminal.

        Args:
            working_directory: Directory to start Claude Code in (defaults to home)
        """
        self.working_directory = working_directory or os.path.expanduser('~')

        # Build environment with proxy settings
        env = os.environ.copy()
        env['ANTHROPIC_BASE_URL'] = f'http://localhost:{self.proxy_port}'
        env['ANTHROPIC_API_KEY'] = self.proxy_token

        logger.info(f"Launching Claude Code in {self.working_directory}")
        logger.info(f"  ANTHROPIC_BASE_URL={env['ANTHROPIC_BASE_URL']}")
        logger.info(f"  ANTHROPIC_API_KEY={self.proxy_token[:20]}...")

        if sys.platform == 'darwin':
            self._launch_macos(env)
        elif sys.platform == 'linux':
            self._launch_linux(env)
        elif sys.platform == 'win32':
            self._launch_windows(env)
        else:
            raise RuntimeError(f"Unsupported platform: {sys.platform}")

        self._launched = True

    def _launch_macos(self, env: dict):
        """Launch in macOS Terminal.app."""
        # Create isolated home directory for this proxy session
        # This ensures Claude Code doesn't find any existing credentials
        import tempfile
        isolated_home = tempfile.mkdtemp(prefix='cc-launcher-home-')

        # Build environment export commands
        # Override HOME to completely isolate from user's config
        # Claude Code looks in ~/.claude/ which will now be empty
        env_exports = (
            f"export HOME='{isolated_home}' && "
            f"export ANTHROPIC_BASE_URL='{env['ANTHROPIC_BASE_URL']}' && "
            f"export ANTHROPIC_API_KEY='{env['ANTHROPIC_API_KEY']}' && "
            f"export DISABLE_AUTOUPDATER=1 && "
            f"export DISABLE_TELEMETRY=1"
        )

        # AppleScript to open Terminal and run claude
        script = f'''
        tell application "Terminal"
            activate
            do script "cd '{self.working_directory}' && {env_exports} && echo '=== CC-Launcher Isolated Session ===' && echo 'Proxy: {env['ANTHROPIC_BASE_URL']}' && echo 'Home: {isolated_home}' && echo '===================================' && claude"
        end tell
        '''

        try:
            subprocess.run(['osascript', '-e', script], check=True)
            logger.info("Claude Code launched in Terminal.app")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to launch Terminal: {e}")
            raise RuntimeError(f"Failed to launch Terminal.app: {e}")
        except FileNotFoundError:
            logger.error("osascript not found - are you on macOS?")
            raise RuntimeError("osascript not found - Terminal launch requires macOS")

    def _launch_linux(self, env: dict):
        """Launch in Linux terminal emulator."""
        # Try common terminal emulators
        terminals = [
            ('gnome-terminal', ['gnome-terminal', '--', 'bash', '-c']),
            ('konsole', ['konsole', '-e', 'bash', '-c']),
            ('xterm', ['xterm', '-e', 'bash', '-c']),
            ('x-terminal-emulator', ['x-terminal-emulator', '-e', 'bash', '-c']),
        ]

        # Build command string
        env_exports = f"export ANTHROPIC_BASE_URL='{env['ANTHROPIC_BASE_URL']}' && export ANTHROPIC_API_KEY='{env['ANTHROPIC_API_KEY']}'"
        cmd_str = f"cd '{self.working_directory}' && {env_exports} && claude; exec bash"

        for name, base_cmd in terminals:
            try:
                # Check if terminal exists
                subprocess.run(['which', name], check=True, capture_output=True)

                # Launch terminal
                full_cmd = base_cmd + [cmd_str]
                subprocess.Popen(full_cmd, env=env)
                logger.info(f"Claude Code launched in {name}")
                return
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue

        raise RuntimeError("No supported terminal emulator found (tried: gnome-terminal, konsole, xterm)")

    def _launch_windows(self, env: dict):
        """Launch in Windows Command Prompt."""
        # Build command
        cmd = f'cd /d "{self.working_directory}" && set ANTHROPIC_BASE_URL={env["ANTHROPIC_BASE_URL"]} && set ANTHROPIC_API_KEY={env["ANTHROPIC_API_KEY"]} && claude'

        try:
            subprocess.Popen(['cmd', '/c', 'start', 'cmd', '/k', cmd], env=env)
            logger.info("Claude Code launched in Command Prompt")
        except FileNotFoundError:
            raise RuntimeError("Failed to launch Command Prompt")

    def is_claude_running(self) -> bool:
        """
        Check if Claude Code was launched.

        Note: This only tracks if we launched it, not if the process is still running.
        """
        return self._launched

    def get_launch_command(self) -> str:
        """Get the command to manually launch Claude Code with proxy settings."""
        return f"""
# Create isolated home to avoid conflicts with your Claude subscription
export HOME=$(mktemp -d)
export ANTHROPIC_BASE_URL='http://localhost:{self.proxy_port}'
export ANTHROPIC_API_KEY='{self.proxy_token}'
export DISABLE_AUTOUPDATER=1
export DISABLE_TELEMETRY=1
claude
""".strip()
