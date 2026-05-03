import type { CodegenConfig } from "@graphql-codegen/cli";

const config: CodegenConfig = {
	overwrite: true,
	schema: "./src/graphql/chat-client-schema.graphql",
	documents: ["./src/graphql/operations/**/*.graphql"],
	generates: {
		"./src/graphql/generated/graphql.ts": {
			plugins: ["typescript-operations"],
			config: {
				enumsAsTypes: true,
				namingConvention: {
					enumValues: "keep",
					typeNames: "keep"
				},
				scalars: {
					Json: "unknown",
					OffsetDateTime: "string",
					RequestCorrelationId: "string | number | null"
				}
			}
		}
	}
};

export default config;
