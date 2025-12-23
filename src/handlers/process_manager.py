"""Process management for launching Claude Code."""

import os
import sys
import subprocess
import shutil
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class ProcessManager:
    """Manages Claude Code process launching."""

    def __init__(self, proxy_port: int, proxy_token: str):
        self.proxy_port = proxy_port
        self.proxy_token = proxy_token
        self.working_directory: Optional[str] = None
        self._launched = False

    def _is_claude_installed(self) -> bool:
        """Check if Claude Code CLI is installed."""
        return shutil.which('claude') is not None

    def _install_claude_code(self) -> Tuple[bool, str]:
        """
        Attempt to install Claude Code via npm.

        Returns:
            Tuple of (success, message)
        """
        # Check if npm is available
        if not shutil.which('npm'):
            return False, "npm not found. Please install Node.js first: https://nodejs.org/"

        logger.info("Installing Claude Code via npm...")

        try:
            # Run npm install globally
            result = subprocess.run(
                ['npm', 'install', '-g', '@anthropic-ai/claude-code'],
                capture_output=True,
                text=True,
                timeout=120  # 2 minute timeout
            )

            if result.returncode == 0:
                logger.info("Claude Code installed successfully")
                return True, "Claude Code installed successfully"
            else:
                error_msg = result.stderr.strip() or result.stdout.strip()
                # Common error: permission denied
                if 'EACCES' in error_msg or 'permission denied' in error_msg.lower():
                    return False, "Permission denied. Try running: sudo npm install -g @anthropic-ai/claude-code"
                return False, f"npm install failed: {error_msg[:200]}"

        except subprocess.TimeoutExpired:
            return False, "Installation timed out. Please install manually: npm install -g @anthropic-ai/claude-code"
        except Exception as e:
            return False, f"Installation failed: {str(e)}"

    def launch_claude_code(self, working_directory: Optional[str] = None) -> Tuple[bool, str]:
        """
        Launch Claude Code in an external terminal.

        Args:
            working_directory: Directory to start Claude Code in (defaults to home)

        Returns:
            Tuple of (success, message)
        """
        # Check if Claude Code is installed
        if not self._is_claude_installed():
            logger.info("Claude Code not found, attempting installation...")
            success, msg = self._install_claude_code()
            if not success:
                return False, msg
            # Verify installation worked
            if not self._is_claude_installed():
                return False, "Installation completed but 'claude' command not found. You may need to restart your terminal or add npm global bin to PATH."

        self.working_directory = working_directory or os.path.expanduser('~')

        # Build environment with proxy settings
        # IMPORTANT: Claude Code uses ANTHROPIC_AUTH_TOKEN (not ANTHROPIC_API_KEY) for custom endpoints
        # See: https://docs.anthropic.com/en/docs/claude-code/settings
        env = os.environ.copy()
        env['ANTHROPIC_BASE_URL'] = f'http://localhost:{self.proxy_port}'
        env['ANTHROPIC_AUTH_TOKEN'] = self.proxy_token
        # Also set API_KEY as fallback for older versions
        env['ANTHROPIC_API_KEY'] = self.proxy_token

        logger.info(f"Launching Claude Code in {self.working_directory}")
        logger.info(f"  ANTHROPIC_BASE_URL={env['ANTHROPIC_BASE_URL']}")
        logger.info(f"  ANTHROPIC_AUTH_TOKEN={self.proxy_token[:20]}...")

        try:
            if sys.platform == 'darwin':
                self._launch_macos(env)
            elif sys.platform == 'linux':
                self._launch_linux(env)
            elif sys.platform == 'win32':
                self._launch_windows(env)
            else:
                return False, f"Unsupported platform: {sys.platform}"
        except RuntimeError as e:
            return False, str(e)

        self._launched = True
        return True, "Claude Code launched successfully"

    def _launch_macos(self, env: dict):
        """Launch in macOS Terminal.app."""
        # Create isolated home directory for this proxy session
        # This ensures Claude Code doesn't find any existing credentials
        import tempfile
        isolated_home = tempfile.mkdtemp(prefix='cc-launcher-home-')

        # Build environment export commands
        # Override HOME to completely isolate from user's config
        # Claude Code looks in ~/.claude/ which will now be empty
        # IMPORTANT: Use ANTHROPIC_AUTH_TOKEN for custom endpoints (not just API_KEY)
        env_exports = (
            f"export HOME='{isolated_home}' && "
            f"export ANTHROPIC_BASE_URL='{env['ANTHROPIC_BASE_URL']}' && "
            f"export ANTHROPIC_AUTH_TOKEN='{env['ANTHROPIC_AUTH_TOKEN']}' && "
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
        # Use both AUTH_TOKEN (for custom endpoints) and API_KEY (fallback)
        env_exports = f"export ANTHROPIC_BASE_URL='{env['ANTHROPIC_BASE_URL']}' && export ANTHROPIC_AUTH_TOKEN='{env['ANTHROPIC_AUTH_TOKEN']}' && export ANTHROPIC_API_KEY='{env['ANTHROPIC_API_KEY']}'"
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
        # Build command - use both AUTH_TOKEN and API_KEY
        cmd = f'cd /d "{self.working_directory}" && set ANTHROPIC_BASE_URL={env["ANTHROPIC_BASE_URL"]} && set ANTHROPIC_AUTH_TOKEN={env["ANTHROPIC_AUTH_TOKEN"]} && set ANTHROPIC_API_KEY={env["ANTHROPIC_API_KEY"]} && claude'

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
# IMPORTANT: Claude Code uses ANTHROPIC_AUTH_TOKEN for custom endpoints
export ANTHROPIC_AUTH_TOKEN='{self.proxy_token}'
export ANTHROPIC_API_KEY='{self.proxy_token}'
export DISABLE_AUTOUPDATER=1
export DISABLE_TELEMETRY=1
claude
""".strip()
