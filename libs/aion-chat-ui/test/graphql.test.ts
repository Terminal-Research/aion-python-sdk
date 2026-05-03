import { describe, expect, it, vi } from "vitest";

import {
	buildAuthenticatedGraphQLWebSocketUrl,
	executeGraphQL,
	GraphQLRequestError
} from "../src/lib/graphql/client.js";
import {
	buildWebAppRouteUrl,
	resolvePostAuthPath,
	runLoginBootstrap
} from "../src/lib/graphql/authBootstrap.js";
import { fetchRegistryAgentIdentities } from "../src/lib/graphql/registry.js";

describe("GraphQL client", () => {
	it("posts authenticated GraphQL queries to the environment API", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(JSON.stringify({ data: { ok: true } }), { status: 200 })
		) as unknown as typeof fetch;

		await expect(
			executeGraphQL<{ ok: boolean }, { id: string }>({
				environmentId: "development",
				accessToken: "access-token",
				query: "query Test($id: ID!) { test(id: $id) }",
				variables: { id: "123" },
				fetchImpl
			})
		).resolves.toEqual({ data: { ok: true } });

		expect(fetchImpl).toHaveBeenCalledWith(
			"http://localhost:8080/api/graphql",
			expect.objectContaining({
				method: "POST",
				body: JSON.stringify({
					query: "query Test($id: ID!) { test(id: $id) }",
					variables: { id: "123" }
				})
			})
		);
		const headers = vi.mocked(fetchImpl).mock.calls[0]?.[1]?.headers as Headers;
		expect(headers.get("Authorization")).toBe("Bearer access-token");
	});

	it("throws graphQL errors even when partial data is returned", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(
				JSON.stringify({
					data: { login: null },
					errors: [{ message: "No access" }]
				}),
				{ status: 200 }
			)
		) as unknown as typeof fetch;

		await expect(
			executeGraphQL({
				environmentId: "development",
				query: "query Test { test }",
				fetchImpl
			})
		).rejects.toThrow(GraphQLRequestError);
	});

	it("adds the bearer token to GraphQL websocket URLs", () => {
		expect(
			buildAuthenticatedGraphQLWebSocketUrl("staging", "token-value")
		).toBe("wss://api-staging.aion.to/ws/graphql?token=token-value");
	});
});

describe("login bootstrap", () => {
	it("runs login bootstrap and resolves onboarding into the web app route", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(
				JSON.stringify({
					data: {
						login: {
							nextRoute: "Onboarding",
							email: " user@example.com ",
							name: " User Name "
						}
					}
				}),
				{ status: 200 }
			)
		) as unknown as typeof fetch;

		const bootstrap = await runLoginBootstrap({
			environmentId: "staging",
			accessToken: "access-token",
			fetchImpl
		});

		expect(bootstrap).toEqual({
			nextRouteKind: "ONBOARDING",
			nextRoutePath: null,
			loginEmail: "user@example.com",
			loginName: "User Name"
		});
		expect(resolvePostAuthPath(bootstrap)).toBe("/onboarding/name");
		expect(buildWebAppRouteUrl("staging", "/onboarding/name")).toBe(
			"https://app-staging.aion.to/onboarding/name"
		);
	});
});

describe("registry GraphQL", () => {
	it("loads current user and A2A agent identities", async () => {
		const fetchImpl = vi
			.fn()
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						data: {
							login: {
								nextRoute: null,
								email: null,
								name: null
							}
						}
					}),
					{ status: 200 }
				)
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						data: {
							user: {
								id: "user-1",
								homeOrganization: { id: "org-1", name: "Org" },
								agentIdentity: {
									id: "personal-1",
									name: "Personal Agent",
									atName: "@me",
									a2aUrl: "https://agent.example/me",
									agentType: "Personal",
									organizationId: "org-1",
									updatedAt: "2026-05-01T00:00:00Z"
								}
							}
						}
					}),
					{ status: 200 }
				)
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						data: {
							agentIdentityDetails: [
								{
									identity: {
										id: "principal-1",
										name: "Team Agent",
										atName: "@team",
										a2aUrl: "https://agent.example/team",
										agentType: "Principal",
										userId: null,
										organizationId: "org-1",
										systemKey: null,
										website: null,
										email: null,
										biography: null,
										avatarImageUrl: null,
										backgroundImageUrl: null,
										updatedAt: "2026-05-01T00:00:00Z",
										notes: null
									},
									distributionUsages: [
										{ distributionId: "dist-1", networkType: "A2A" }
									]
								}
							]
						}
					}),
					{ status: 200 }
				)
			) as unknown as typeof fetch;

		await expect(
			fetchRegistryAgentIdentities({
				environmentId: "development",
				accessToken: "access-token",
				fetchImpl
			})
		).resolves.toEqual([
			{
				id: "personal-1",
				name: "Personal Agent",
				atName: "@me",
				a2aUrl: "https://agent.example/me",
				updatedAt: "2026-05-01T00:00:00Z"
			},
			{
				id: "principal-1",
				name: "Team Agent",
				atName: "@team",
				a2aUrl: "https://agent.example/team",
				updatedAt: "2026-05-01T00:00:00Z"
			}
		]);
	});
});
