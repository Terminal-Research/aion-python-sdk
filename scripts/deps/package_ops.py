"""
Internal module for package operations.
"""

import subprocess
from pathlib import Path
from typing import Tuple, Optional

from config import LIBS_DIR


def check_poetry_available() -> bool:
    """
    Check if Poetry is available on the system and print version info.

    Returns:
        True if Poetry is available, False otherwise
    """
    try:
        result = subprocess.run(['poetry', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"[INFO] Using {result.stdout.strip()}")
            return True
        return False
    except FileNotFoundError:
        return False


def run_command(
    command: list,
    package_dir: Path,
    timeout: int = 300,
    capture_output: bool = True
) -> Tuple[bool, str, str]:
    """
    Execute a shell command in a specific directory.

    The command runs with `cwd` set to the package directory so that Poetry
    picks up that package's `poetry.toml`, which is resolved relative to the
    working directory rather than to the project being operated on.

    Args:
        command: List of command arguments
        package_dir: Directory to run the command in
        timeout: Command timeout in seconds (default: 300)
        capture_output: Whether to capture stdout/stderr (default: True)

    Returns:
        Tuple of (success, stdout, stderr)
    """
    try:
        result = subprocess.run(
            command,
            cwd=package_dir,
            capture_output=capture_output,
            text=True,
            timeout=timeout
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"Command timed out after {timeout} seconds"
    except Exception as e:
        return False, "", str(e)


def _handle_command_result(
    success: bool,
    stdout: str,
    stderr: str,
    command_name: str,
    success_message: str,
    error_prefix: str = "  [ERROR]"
) -> bool:
    """
    Handle command execution result with consistent error reporting.

    Both streams are reported on failure. Poetry writes most of its diagnostics
    to stdout rather than stderr, so surfacing stderr alone leaves failures with
    no explanation at all.

    Args:
        success: Whether command succeeded
        stdout: Standard output, reported alongside stderr on failure
        stderr: Standard error output
        command_name: Name of the command for error messages
        success_message: Message to display on success
        error_prefix: Prefix for error messages (default: "  [ERROR]")

    Returns:
        The success value (pass-through for convenience)
    """
    if success:
        print(f"  [SUCCESS] {success_message}")
    else:
        print(f"{error_prefix} {command_name} failed")
        for stream in (stdout, stderr):
            if stream and stream.strip():
                print(f"  {stream.strip()}")
    return success


def validate_package(package_name: str, package_dir: Optional[Path] = None) -> Tuple[bool, Optional[Path]]:
    """
    Validate that a package exists and has required files.

    Args:
        package_name: Name of the package to validate
        package_dir: Optional path to package directory (will be constructed if not provided)

    Returns:
        Tuple of (is_valid, package_dir)
    """
    if package_dir is None:
        package_dir = LIBS_DIR / package_name

    if not package_dir.exists():
        return False, None

    if not (package_dir / 'pyproject.toml').exists():
        return False, None

    return True, package_dir


def execute_poetry_command(
    package_name: str,
    command: list,
    action_description: str,
    success_message: str,
    timeout: int = 300
) -> bool:
    """
    Execute a Poetry command for a single package.

    Universal function to run any Poetry command on a package with
    consistent error handling and logging.

    Args:
        package_name: Name of the package to process
        command: List of command arguments (e.g., ['lock'], ['install'])
        action_description: Description of the action for logging (e.g., "locking dependencies")
        success_message: Message to display on success (e.g., "Lock file updated")
        timeout: Command timeout in seconds (default: 300)

    Returns:
        True if command succeeded, False otherwise

    Example:
        execute_poetry_command('aion-core', ['lock'], 'locking dependencies', 'Lock file updated')
        execute_poetry_command('aion-server', ['sync'], 'syncing dependencies', 'Dependencies synced')
    """
    is_valid, package_dir = validate_package(package_name)

    if not is_valid:
        print(f"[SKIP] {package_name}: invalid package")
        return False

    print(f"\n[PACKAGE] {package_name}: {action_description}")

    full_command = ['poetry'] + command
    success, stdout, stderr = run_command(full_command, package_dir, timeout=timeout)

    return _handle_command_result(
        success,
        stdout,
        stderr,
        f"Poetry {' '.join(command)}",
        success_message
    )


def validate_libs_dir() -> bool:
    """
    Validate that the libs directory exists.

    Returns:
        True if libs directory exists, False otherwise
    """
    return LIBS_DIR.exists()
