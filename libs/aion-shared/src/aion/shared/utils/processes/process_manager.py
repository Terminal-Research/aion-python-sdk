import multiprocessing
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Callable, Any, Optional, Tuple

from aion.shared.logging import get_logger

logger = get_logger()


class ProcessStatus(Enum):
    """Process status enumeration"""
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    TERMINATED = "terminated"


@dataclass
class ProcessInfo:
    """Information about managed process"""
    key: str
    process: multiprocessing.Process
    target_function: Callable
    args: Tuple
    kwargs: Dict[str, Any]
    created_at: float
    status: ProcessStatus
    pid: Optional[int] = None


class ProcessManager:
    """
    Manager for handling multiple processes with custom keys.
    Designed for serving A2A (Agent-to-Agent) LangGraph agents.
    """

    def __init__(self):
        self.processes: Dict[str, ProcessInfo] = {}

    def create_process(
            self,
            key: str,
            target_function: Callable,
            *args,
            **kwargs
    ) -> bool:
        """
        Create and start a new process with custom key

        Args:
            key: Custom key to identify the process
            target_function: Function to run in separate process
            *args: Positional arguments for target function
            **kwargs: Keyword arguments for target function

        Returns:
            bool: True if process created successfully, False otherwise
        """
        if key in self.processes:
            logger.warning(f"Process with key '{key}' already exists")
            return False

        try:
            # Create new process
            process = multiprocessing.Process(
                target=target_function,
                args=args,
                kwargs=kwargs,
                name=f"{key}"
            )

            # Start the process
            process.start()

            # Store process information
            process_info = ProcessInfo(
                key=key,
                process=process,
                target_function=target_function,
                args=args,
                kwargs=kwargs,
                created_at=time.time(),
                status=ProcessStatus.RUNNING,
                pid=process.pid
            )

            self.processes[key] = process_info
            logger.info(f"Process '{key}' created with PID {process.pid}")

            return True

        except Exception as e:
            logger.error(f"Failed to create process '{key}': {str(e)}")
            return False

    def terminate_process(self, key: str, timeout: float = 5.0) -> bool:
        """
        Terminate process by key

        Args:
            key: Process key to terminate
            timeout: Timeout in seconds for graceful termination

        Returns:
            bool: True if process terminated successfully, False otherwise
        """
        if key not in self.processes:
            logger.warning(f"Process with key '{key}' not found")
            return False

        process_info = self.processes[key]
        process = process_info.process

        if not process.is_alive():
            logger.info(f"Process '{key}' is already terminated")
            process_info.status = ProcessStatus.STOPPED
            return True

        try:
            # Try graceful termination first
            process.terminate()
            process.join(timeout=timeout)

            # If still alive, force kill
            if process.is_alive():
                logger.warning(f"Force killing process '{key}'")
                process.kill()
                process.join()

            process_info.status = ProcessStatus.TERMINATED
            logger.info(f"Process '{key}' terminated successfully")

            return True

        except Exception as e:
            logger.error(f"Failed to terminate process '{key}': {str(e)}")
            process_info.status = ProcessStatus.ERROR
            return False

    def remove_process(self, key: str) -> bool:
        """
        Remove process from manager (terminates if running)

        Args:
            key: Process key to remove

        Returns:
            bool: True if process removed successfully, False otherwise
        """
        if key not in self.processes:
            logger.warning(f"Process with key '{key}' not found")
            return False

        # Terminate process if still running
        if self.processes[key].process.is_alive():
            self.terminate_process(key)

        # Remove from tracking
        del self.processes[key]
        logger.info(f"Process '{key}' removed from manager")

        return True

    def get_process_info(self, key: str) -> Optional[ProcessInfo]:
        """
        Get information about specific process

        Args:
            key: Process key

        Returns:
            ProcessInfo: Process information or None if not found
        """
        return self.processes.get(key)

    def list_processes(self) -> Dict[str, Dict[str, Any]]:
        """
        List all managed processes with their status

        Returns:
            Dict: Dictionary with process information
        """
        result = {}

        for key, process_info in self.processes.items():
            # Update status based on current state
            if process_info.process.is_alive():
                process_info.status = ProcessStatus.RUNNING
            else:
                if process_info.status == ProcessStatus.RUNNING:
                    process_info.status = ProcessStatus.STOPPED

            result[key] = {
                "pid": process_info.pid,
                "status": process_info.status.value,
                "created_at": process_info.created_at,
                "uptime": time.time() - process_info.created_at,
                "is_alive": process_info.process.is_alive(),
                "function_name": process_info.target_function.__name__
            }

        return result

    def cleanup_dead_processes(self) -> int:
        """
        Remove dead processes from manager

        Returns:
            int: Number of cleaned up processes
        """
        dead_keys = []

        for key, process_info in self.processes.items():
            if not process_info.process.is_alive():
                dead_keys.append(key)

        for key in dead_keys:
            self.remove_process(key)

        cleaned_up_count = len(dead_keys)
        if cleaned_up_count:
            logger.info(f"Cleaned up {cleaned_up_count} dead processes")
        return len(dead_keys)

    def shutdown_all(self, timeout: float = 5.0) -> bool:
        """
        Shutdown all managed processes

        Args:
            timeout: Timeout for each process termination

        Returns:
            bool: True if all processes shut down successfully
        """
        success = True
        keys_to_remove = list(self.processes.keys())

        for key in keys_to_remove:
            if not self.terminate_process(key, timeout):
                success = False

        # Clear all processes
        self.processes.clear()
        logger.info("All processes shut down")

        return success

    def restart_process(self, key: str, timeout: float = 5.0) -> bool:
        """
        Restart a specific process

        Args:
            key: Process key to restart
            timeout: Timeout for termination

        Returns:
            bool: True if process restarted successfully
        """
        if key not in self.processes:
            logger.warning(f"Process with key '{key}' not found")
            return False

        process_info = self.processes[key]

        # Store original parameters
        target_function = process_info.target_function
        args = process_info.args
        kwargs = process_info.kwargs

        # Terminate current process
        if not self.terminate_process(key, timeout):
            logger.error(f"Failed to terminate process '{key}' for restart")
            return False

        # Remove old process
        del self.processes[key]

        # Create new process with same parameters
        return self.create_process(key, target_function, *args, **kwargs)

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup all processes"""
        self.shutdown_all()
