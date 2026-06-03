import type {
	Message,
	Part,
	StreamResponse,
	Task,
	TaskArtifactUpdateEvent,
	TaskStatusUpdateEvent
} from "@a2a-js/sdk";
import { Role, TaskState } from "@a2a-js/sdk";

export type StreamEvent = Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent;

const TERMINAL_TASK_STATES = new Set<TaskState>([
	TaskState.TASK_STATE_COMPLETED,
	TaskState.TASK_STATE_CANCELED,
	TaskState.TASK_STATE_FAILED,
	TaskState.TASK_STATE_REJECTED
]);

const LEGACY_TERMINAL_TASK_STATES = new Set([
	"completed",
	"canceled",
	"failed",
	"rejected"
]);

export function makeTextPart(text: string): Part {
	return {
		content: { $case: "text", value: text },
		metadata: undefined,
		filename: "",
		mediaType: "text/plain"
	};
}

export function makeRawFilePart({
	filename,
	mediaType,
	bytes
}: {
	filename: string;
	mediaType: string;
	bytes: Buffer;
}): Part {
	return {
		content: { $case: "raw", value: bytes },
		metadata: undefined,
		filename,
		mediaType
	};
}

export function isMessage(value: unknown): value is Message {
	return Boolean(
		value &&
		typeof value === "object" &&
		"messageId" in value &&
		"role" in value &&
		"parts" in value
	);
}

export function isTask(value: unknown): value is Task {
	return Boolean(
		value &&
		typeof value === "object" &&
		"id" in value &&
		"status" in value &&
		"history" in value
	);
}

export function isTaskStatusUpdateEvent(value: unknown): value is TaskStatusUpdateEvent {
	return Boolean(
		value &&
		typeof value === "object" &&
		"taskId" in value &&
		"contextId" in value &&
		"status" in value &&
		!("artifact" in value)
	);
}

export function isTaskArtifactUpdateEvent(value: unknown): value is TaskArtifactUpdateEvent {
	return Boolean(
		value &&
		typeof value === "object" &&
		"taskId" in value &&
		"contextId" in value &&
		"artifact" in value
	);
}

export function unwrapStreamResponse(response: StreamResponse | StreamEvent): StreamEvent | undefined {
	if (isMessage(response) || isTask(response) || isTaskStatusUpdateEvent(response) || isTaskArtifactUpdateEvent(response)) {
		return response;
	}
	return response.payload?.value;
}

export function isAgentMessage(message: Message): boolean {
	return message.role === Role.ROLE_AGENT || (message.role as unknown) === "agent";
}

export function isTerminalTaskState(state: TaskState | string | undefined): boolean {
	if (state === undefined) {
		return false;
	}
	if (typeof state === "string") {
		return LEGACY_TERMINAL_TASK_STATES.has(state);
	}
	return TERMINAL_TASK_STATES.has(state);
}

export function taskStateLabel(state: TaskState | string | undefined): string {
	if (state === undefined) {
		return "unknown";
	}
	if (typeof state === "string") {
		return state;
	}
	switch (state) {
		case TaskState.TASK_STATE_SUBMITTED:
			return "submitted";
		case TaskState.TASK_STATE_WORKING:
			return "working";
		case TaskState.TASK_STATE_COMPLETED:
			return "completed";
		case TaskState.TASK_STATE_FAILED:
			return "failed";
		case TaskState.TASK_STATE_CANCELED:
			return "canceled";
		case TaskState.TASK_STATE_INPUT_REQUIRED:
			return "input-required";
		case TaskState.TASK_STATE_REJECTED:
			return "rejected";
		case TaskState.TASK_STATE_AUTH_REQUIRED:
			return "auth-required";
		default:
			return "unknown";
	}
}
