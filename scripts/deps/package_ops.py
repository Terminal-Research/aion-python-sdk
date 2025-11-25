"""
Internal module for package operations.
"""

import os
import subprocess
from pathlib import Path
from typing import Tuple, Optional

from config import PACKAGES, LIBS_DIR


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
    stderr: str,
    command_name: str,
    success_message: str,
    error_prefix: str = "  [ERROR]"
) -> bool:
    """
    Handle command execution result with consistent error reporting.

    Args:
        success: Whether command succeeded
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
        if stderr:
            print(f"  {stderr.strip()}")
    return success


def ensure_poetry_environment(package_dir: Path) -> bool:
    """
    Ensure a Poetry environment exists for a package.

    If the environment doesn't exist or is broken, initializes it by running
    `poetry install`.

    Args:
        package_dir: Path to the package directory

    Returns:
        True if environment is ready, False otherwise
    """
    try:
        result = subprocess.run(
            ['poetry', 'env', 'info', '--path'],
            cwd=package_dir,
            capture_output=True,
            text=True
        )

        if result.returncode == 0 and result.stdout.strip():
            test_result = subprocess.run(
                ['poetry', 'run', 'python', '--version'],
                cwd=package_dir,
                capture_output=True,
                text=True
            )
            if test_result.returncode == 0:
                return True

        print(f"  [SETUP] Initializing Poetry environment for {package_dir.name}")
        init_result = subprocess.run(
            ['poetry', 'install'],
            cwd=package_dir,
            capture_output=True,
            text=True
        )
        return init_result.returncode == 0

    except Exception as e:
        print(f"  [ERROR] Failed to initialize Poetry environment: {e}")
        return False


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


def install_package_editable(dep_name: str, package_dir: Path) -> bool:
    """
    Install a local package in editable mode using pip.

    Runs `poetry run pip install -e <relative_path>` to install the dependency
    in editable/development mode, allowing live code changes.

    Args:
        dep_name: Name of the dependency package to install
        package_dir: Directory of the parent package where dependency will be installed

    Returns:
        True if installation succeeded, False otherwise
    """
    is_valid, dep_path = validate_package(dep_name)

    if not is_valid:
        print(f"  [ERROR] Invalid package: {dep_name}")
        return False

    relative_path = os.path.relpath(dep_path, package_dir)
    cmd = ['poetry', 'run', 'pip', 'install', '-e', relative_path]

    success, stdout, stderr = run_command(cmd, package_dir)

    return _handle_command_result(
        success,
        stderr,
        f"pip install -e {dep_name}",
        f"Installed {dep_name} < {dep_path}"
    )


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
        command: List of command arguments (e.g., ['lock'], ['install', '--sync'])
        action_description: Description of the action for logging (e.g., "locking dependencies")
        success_message: Message to display on success (e.g., "Lock file updated")
        timeout: Command timeout in seconds (default: 300)

    Returns:
        True if command succeeded, False otherwise

    Example:
        execute_poetry_command('aion-cli', ['lock'], 'locking dependencies', 'Lock file updated')
        execute_poetry_command('aion-server', ['install', '--sync'], 'syncing dependencies', 'Dependencies synced')
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
        stderr,
        f"Poetry {' '.join(command)}",
        success_message
    )


def get_all_packages():
    """
    Get all package names from configuration.

    Returns:
        List of package names
    """
    return list(PACKAGES.keys())


def validate_libs_dir() -> bool:
    """
    Validate that the libs directory exists.

    Returns:
        True if libs directory exists, False otherwise
    """
    return LIBS_DIR.exists()
