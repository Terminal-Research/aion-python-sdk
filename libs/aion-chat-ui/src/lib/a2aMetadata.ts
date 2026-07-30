import crypto from "node:crypto";

export const DISTRIBUTION_EXTENSION_URI_V1 =
	"https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0";
export const TRACEABILITY_EXTENSION_URI_V1 =
	"https://docs.aion.to/a2a/extensions/aion/traceability/1.0.0";
export const STREAM_DELTA_ARTIFACT_ID = "aion:stream-delta";
export const THINKING_DELTA_ARTIFACT_ID = "aion:thinking-delta";
export const EPHEMERAL_MESSAGE_ARTIFACT_ID = "aion:ephemeral-message";

export interface MetadataOptions {
	agentName?: string;
	agentUsername?: string;
	behaviorKey?: string;
	environmentName?: string;
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
			senderId,
			distribution: {
				id: crypto.randomUUID(),
				endpointType: "Aion",
				url: "https://example.com/agent-card",
				identities: [
					{
						kind: "principal",
						id: crypto.randomUUID(),
						identityNetwork: "Aion",
						identityKind: "Personal",
						representedUserId: crypto.randomUUID(),
						organizationId: orgId,
						displayName: agentName,
						userName: agentUsername,
						avatarImageUrl: "https://example.com/avatar.png",
						agentType: "Personal",
						url: "https://example.com/agent"
					}
				]
			},
			behavior: {
				id: crypto.randomUUID(),
				behaviorKey: behaviorKey,
				versionId: crypto.randomUUID()
			},
			environment: {
				id: crypto.randomUUID(),
				name: environmentName,
				projectId: crypto.randomUUID(),
				deploymentId: crypto.randomUUID(),
				configurationVariables: {
					API_TIMEOUT: "30",
					MAX_RETRIES: "3",
					LOG_LEVEL: "INFO"
				}
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
