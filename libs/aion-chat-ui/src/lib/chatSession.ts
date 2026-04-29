import type { Message, Task } from "@a2a-js/sdk";

import { getTaskMessages } from "./messageDisplay.js";
import type { ResponseMode } from "./slashCommands.js";

const NO_TASK_ID = "no-task";

export interface ShownMessageReference {
	taskId?: string;
	messageId: string;
}

export function getMessageTaskId(
	message: Message,
	fallbackTaskId?: string
): string | undefined {
	return message.taskId ?? fallbackTaskId;
}

export function createShownMessageKey(reference: ShownMessageReference): string {
	return `${reference.taskId ?? NO_TASK_ID}:${reference.messageId}`;
}

export function getShownMessageKey(
	message: Message,
	fallbackTaskId?: string
): string {
	return createShownMessageKey({
		taskId: getMessageTaskId(message, fallbackTaskId),
		messageId: message.messageId
	});
}

export function hasShownMessage(
	shownMessageKeys: ReadonlySet<string>,
	message: Message,
	fallbackTaskId?: string
): boolean {
	return shownMessageKeys.has(getShownMessageKey(message, fallbackTaskId));
}

export function markShownMessage(
	shownMessageKeys: Set<string>,
	message: Message,
	fallbackTaskId?: string
): string {
	const key = getShownMessageKey(message, fallbackTaskId);
	shownMessageKeys.add(key);
	return key;
}

export function getUnshownTaskAgentMessages(
	task: Task,
	shownMessageKeys: ReadonlySet<string>
): Message[] {
	return getTaskMessages(task).filter(
		(message) =>
			message.role === "agent" && !hasShownMessage(shownMessageKeys, message, task.id)
	);
}

export function shouldRenderLiveResponseMessage(message: Message): boolean {
	return message.role === "agent";
}

export function shouldRenderLiveStatusMessage({
	message,
	taskId,
	streamedTaskIds
}: {
	message: Message | undefined;
	taskId: string;
	streamedTaskIds: ReadonlySet<string>;
}): boolean {
	return Boolean(
		message &&
		shouldRenderLiveResponseMessage(message) &&
		!streamedTaskIds.has(taskId)
	);
}

export function shouldShowNoAgentMessageNotice({
	responseMode,
	reachedTerminal,
	renderedAgentOutput
}: {
	responseMode: ResponseMode;
	reachedTerminal: boolean;
	renderedAgentOutput: boolean;
}): boolean {
	return (
		responseMode !== "a2a-protocol" &&
		reachedTerminal &&
		!renderedAgentOutput
	);
}
