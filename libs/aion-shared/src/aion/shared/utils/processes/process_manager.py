from __future__ import annotations

import multiprocessing
import time
from dataclasses import dataclass
from enum import Enum
from multiprocessing.connection import Connection
from typing import Dict, Callable, Any, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from aion.shared.logging.base import AionLogger


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
    parent_conn: Optional[Connection] = None
    child_conn: Optional[Connection] = None


class ProcessManager:
    """
    Manager for handling multiple processes with custom keys.
    Designed for serving A2A (Agent-to-Agent) LangGraph agents.
    """

    def __init__(self, logger: Optional[AionLogger] = None):
        self.processes: Dict[str, ProcessInfo] = {}
        self._logger: Optional[AionLogger] = logger

    @property
    def logger(self) -> AionLogger:
        if not self._logger:
            from aion.shared.logging.factory import get_logger
            self._logger = get_logger()
        return self._logger

    def create_process(
            self,
            key: str,
            func: Callable,
            func_args: Optional[Tuple] = None,
            func_kwargs: Optional[Dict[str, Any]] = None,
            use_pipe: bool = False
    ) -> bool:
        """
        Create and start a new process with custom key

        Args:
            key: Custom key to identify the process
            func: Function to run in separate process
            func_args: Positional arguments for target function
            func_kwargs: Keyword arguments for target function
            use_pipe: If True, creates a bidirectional pipe for parent-child communication

        Returns:
            bool: True if process created successfully, False otherwise
        """
        if key in self.processes:
            self.logger.warning(f"Process with key '{key}' already exists")
            return False

        # Initialize args and kwargs if not provided
        if func_args is None:
            func_args = ()
        if func_kwargs is None:
            func_kwargs = {}

        try:
            parent_conn = None
            child_conn = None

            # Create pipe if requested
            if use_pipe:
                parent_conn, child_conn = multiprocessing.Pipe()
                # Add child_conn to kwargs if target function expects it
                func_kwargs['conn'] = child_conn

            # Create new process
            process = multiprocessing.Process(
                target=func,
                args=func_args,
                kwargs=func_kwargs,
                name=f"{key}"
            )

            # Start the process
            process.start()

            # Store process information
            process_info = ProcessInfo(
                key=key,
                process=process,
                target_function=func,
                args=func_args,
                kwargs=func_kwargs,
                created_at=time.time(),
                status=ProcessStatus.RUNNING,
                pid=process.pid,
                parent_conn=parent_conn,
                child_conn=child_conn
            )

            self.processes[key] = process_info
            self.logger.debug(f"Process '{key}' created with PID {process.pid}{' (with pipe)' if use_pipe else ''}")

            return True

        except Exception as e:
            self.logger.exception(f"Failed to create process '{key}': {str(e)}")
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
            self.logger.warning(f"Process with key '{key}' not found")
            return False

        process_info = self.processes[key]
        process = process_info.process

        if not process.is_alive():
            self.logger.debug(f"Process '{key}' is already terminated")
            process_info.status = ProcessStatus.STOPPED
            # Close pipe connections if they exist
            self._close_pipes(process_info)
            return True

        try:
            # Try graceful termination first
            process.terminate()
            process.join(timeout=timeout)

            # If still alive, force kill
            if process.is_alive():
                self.logger.warning(f"Force killing process '{key}'")
                process.kill()
                process.join()

            process_info.status = ProcessStatus.TERMINATED
            self.logger.debug(f"Process '{key}' terminated successfully")

            # Close pipe connections if they exist
            self._close_pipes(process_info)

            return True

        except Exception as e:
            self.logger.error(f"Failed to terminate process '{key}': {str(e)}")
            process_info.status = ProcessStatus.ERROR
            return False

    def _close_pipes(self, process_info: ProcessInfo) -> None:
        """
        Close pipe connections for a process

        Args:
            process_info: Process information
        """
        try:
            if process_info.parent_conn is not None:
                process_info.parent_conn.close()
                self.logger.debug(f"Closed parent connection for process '{process_info.key}'")
            if process_info.child_conn is not None:
                process_info.child_conn.close()
                self.logger.debug(f"Closed child connection for process '{process_info.key}'")
        except Exception as e:
            self.logger.warning(f"Error closing pipes for process '{process_info.key}': {str(e)}")

    def remove_process(self, key: str) -> bool:
        """
        Remove process from manager (terminates if running)

        Args:
            key: Process key to remove

        Returns:
            bool: True if process removed successfully, False otherwise
        """
        if key not in self.processes:
            self.logger.warning(f"Process with key '{key}' not found")
            return False

        # Terminate process if still running
        if self.processes[key].process.is_alive():
            self.terminate_process(key)

        # Remove from tracking
        del self.processes[key]
        self.logger.info(f"Process '{key}' removed from manager")

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

    def send_to_process(self, key: str, message: Any) -> bool:
        """
        Send message to child process via pipe

        Args:
            key: Process key
            message: Message to send

        Returns:
            bool: True if message sent successfully, False otherwise
        """
        if key not in self.processes:
            self.logger.warning(f"Process with key '{key}' not found")
            return False

        process_info = self.processes[key]

        if process_info.parent_conn is None:
            self.logger.error(f"Process '{key}' does not have a pipe connection")
            return False

        try:
            process_info.parent_conn.send(message)
            self.logger.debug(f"Message sent to process '{key}'")
            return True
        except Exception as e:
            self.logger.error(f"Failed to send message to process '{key}': {str(e)}")
            return False

    def receive_from_process(self, key: str, timeout: Optional[float] = None) -> Optional[Any]:
        """
        Receive message from child process via pipe

        Args:
            key: Process key
            timeout: Timeout in seconds (None for blocking, 0 for non-blocking)

        Returns:
            Message from child process or None if no message/error
        """
        if key not in self.processes:
            self.logger.warning(f"Process with key '{key}' not found")
            return None

        process_info = self.processes[key]

        if process_info.parent_conn is None:
            self.logger.error(f"Process '{key}' does not have a pipe connection")
            return None

        try:
            if timeout is not None:
                if process_info.parent_conn.poll(timeout):
                    message = process_info.parent_conn.recv()
                    return message
                else:
                    return None
            else:
                message = process_info.parent_conn.recv()
                return message
        except Exception as e:
            self.logger.error(f"Failed to receive message from process '{key}': {str(e)}")
            return None

    def get_connection(self, key: str) -> Optional[Connection]:
        """
        Get parent connection for direct communication with child process

        Args:
            key: Process key

        Returns:
            Connection: Parent connection or None if not found/not available
        """
        if key not in self.processes:
            self.logger.warning(f"Process with key '{key}' not found")
            return None

        process_info = self.processes[key]
        return process_info.parent_conn

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
            self.logger.debug(f"Cleaned up {cleaned_up_count} dead processes")
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
        self.logger.debug("All processes shut down")

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
            self.logger.warning(f"Process with key '{key}' not found")
            return False

        process_info = self.processes[key]

        # Store original parameters
        target_function = process_info.target_function
        args = process_info.args
        kwargs = process_info.kwargs

        # Terminate current process
        if not self.terminate_process(key, timeout):
            self.logger.error(f"Failed to terminate process '{key}' for restart")
            return False

        # Remove old process
        del self.processes[key]

        # Create new process with same parameters
        return self.create_process(
            key=key,
            func=target_function,
            func_args=args,
            func_kwargs=kwargs
        )

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup all processes"""
        self.shutdown_all()
