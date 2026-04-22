import crypto from "node:crypto";

export const DISTRIBUTION_EXTENSION_URI_V1 =
	"https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0";
export const TRACEABILITY_EXTENSION_URI_V1 =
	"https://docs.aion.to/a2a/extensions/aion/traceability/1.0.0";
export const STREAM_DELTA_ARTIFACT_ID = "aion:stream-delta";
export const EPHEMERAL_MESSAGE_ARTIFACT_ID = "aion:ephemeral-message";

export interface MetadataOptions {
	agentName?: string;
	agentUsername?: string;
	behaviorKey?: string;
	environmentName?: string;
	systemPrompt?: string;
	senderId?: string;
	nodeId?: string;
}

function tokenHex(bytes: number): string {
	return crypto.randomBytes(bytes).toString("hex");
}

export function generateTaskMetadata(
	options: MetadataOptions = {}
): Record<string, unknown> {
	const senderId = options.senderId ?? "aion:user:2244994945";
	const nodeId = options.nodeId ?? "cli-node-local";
	const agentName = options.agentName ?? "Test Agent";
	const agentUsername = options.agentUsername ?? "testagent";
	const behaviorKey = options.behaviorKey ?? "testGraph";
	const environmentName = options.environmentName ?? "Development";
	const traceId = tokenHex(16);
	const spanId = tokenHex(8);
	const orgId = crypto.randomUUID();

	return {
		[DISTRIBUTION_EXTENSION_URI_V1]: {
			sender_id: senderId,
			distribution: {
				id: crypto.randomUUID(),
				endpoint_type: "Aion",
				url: "https://example.com/agent-card",
				identities: [
					{
						kind: "principal",
						id: crypto.randomUUID(),
						network_type: "Aion",
						represented_user_id: crypto.randomUUID(),
						organization_id: orgId,
						display_name: agentName,
						user_name: agentUsername,
						avatar_image_url: "https://example.com/avatar.png",
						agent_type: "Deployed",
						url: "https://example.com/agent"
					}
				]
			},
			behavior: {
				id: crypto.randomUUID(),
				behavior_key: behaviorKey,
				version_id: crypto.randomUUID()
			},
			environment: {
				id: crypto.randomUUID(),
				name: environmentName,
				deployment_id: crypto.randomUUID(),
				configuration_variables: {
					API_TIMEOUT: "30",
					MAX_RETRIES: "3",
					LOG_LEVEL: "INFO"
				},
				system_prompt:
					options.systemPrompt ?? `You are ${agentName}, a helpful assistant.`
			}
		},
		[TRACEABILITY_EXTENSION_URI_V1]: {
			traceparent: `00-${traceId}-${spanId}-01`,
			tracestate: [{ key: "aion", value: spanId }],
			baggage: {
				"aion.sender.id": nodeId,
				channel: "cli",
				tenant: "local"
			}
		}
	};
}
