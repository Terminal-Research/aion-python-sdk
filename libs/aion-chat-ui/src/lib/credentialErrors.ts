import type { RuntimeAgentSource } from "./agents/model.js";

/** A source refused a request before acquiring or sending its credentials. */
export class CredentialDestinationError extends Error {
	constructor(
		source: Pick<RuntimeAgentSource, "sourceKey" | "description">,
		readonly trustedOrigin: string,
		readonly requestedOrigin: string,
		includesUserInfo = false
	) {
		const reason = includesUserInfo
			? "URLs containing a username or password are not allowed. "
			: "The requested origin does not match this source. ";
		super(
			`Blocked request to ${requestedOrigin}. ` +
			`Trusted credential destination for "${source.description}": ${trustedOrigin}. ` +
			reason + "No credentials were sent. Check the source URL and agent-card URL."
		);
		this.name = "CredentialDestinationError";
	}
}
