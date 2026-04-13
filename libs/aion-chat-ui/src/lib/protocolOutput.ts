import { stringify as stringifyYaml } from "yaml";

function toPlainValue(value: unknown): unknown {
	try {
		return JSON.parse(JSON.stringify(value)) as unknown;
	} catch {
		return value;
	}
}

export function formatProtocolPayload(payload: unknown): string {
	const yaml = stringifyYaml(toPlainValue(payload)).trimEnd();
	return `\`\`\`yaml\n${yaml}\n\`\`\``;
}
