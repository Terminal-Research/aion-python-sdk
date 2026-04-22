import type { TaskState } from "@a2a-js/sdk";

const TERMINAL_TASK_STATES: readonly TaskState[] = [
	"completed",
	"canceled",
	"failed",
	"rejected"
] as const;

/**
 * Returns whether the given task state is terminal.
 *
 * Terminal task states should not be reused for future requests because the
 * server will reject attempts to continue a finished task.
 */
export function isTerminalTaskState(state: TaskState): boolean {
	return TERMINAL_TASK_STATES.includes(state);
}
