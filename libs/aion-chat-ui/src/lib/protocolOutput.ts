import { stringify as stringifyYaml } from "yaml";

function toPlainValue(value: unknown): unknown {
	try {
		return JSON.parse(JSON.stringify(value)) as unknown;
	} catch {
		return String(value);
	}
}

export function formatProtocolPayloadAsJson(payload: unknown): string {
	return JSON.stringify(toPlainValue(payload), null, 2);
}

export function formatProtocolPayload(payload: unknown): string {
	const yaml = stringifyYaml(toPlainValue(payload)).trimEnd();
	return `\`\`\`yaml\n${yaml}\n\`\`\``;
}
