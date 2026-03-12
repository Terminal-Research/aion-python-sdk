# Compatibility shims between a2a protocol v0.3 and v1.0.
#
# v03_to_v1/  — outgoing: rewrites v0.3 wire format to v1.0 before responses leave the server.
#
#   Parts
#     {"kind":"text","text":"…"}                             > {"text":"…"}
#     {"kind":"data","data":{…}}                             > {"data":{…}}
#     {"kind":"file","file":{"bytes":"…","mimeType":"…",…}}  > {"raw":"…","mediaType":"…",…}
#     {"kind":"file","file":{"uri":"…","mimeType":"…"}}      > {"url":"…","mediaType":"…"}
#
#   Streaming events (result field)
#     {"kind":"status-update",  …} > {"statusUpdate":  {…}}
#     {"kind":"artifact-update",…} > {"artifactUpdate":{…}}
#
#   Other objects
#     Message.kind stripped; Task.kind stripped + history/artifacts recursed
#
#   AgentCard
#     authentication           > securitySchemes / securityRequirements
#     additionalInterfaces     > supportedInterfaces (JSONRPC only)
#     supportsAuthenticatedExtendedCard > capabilities.extendedAgentCard
#
# v1_to_v03/  — incoming: rewrites v1.0 wire format to v0.3 before requests reach the agent.
#
#   Parts
#     {"text":"…"}                             > {"kind":"text","text":"…"}
#     {"data":{…}}                             > {"kind":"data","data":{…}}
#     {"raw":"…","mediaType":"…",…}            > {"kind":"file","file":{"bytes":"…","mimeType":"…",…}}
#     {"url":"…","mediaType":"…"}              > {"kind":"file","file":{"uri":"…","mimeType":"…"}}
#
#   Streaming events (params field)
#     {"statusUpdate":  {…}} > {"kind":"status-update",  …}
#     {"artifactUpdate":{…}} > {"kind":"artifact-update",…}
#
#   Other objects
#     Message gets kind:"message"; Task gets kind:"task" + history/artifacts recursed
#
# To remove when upgrading a2a-sdk to >= 1.0:
#   1. Delete src/aion/server/compat/
#   2. Delete src/aion/server/core/middlewares/a2a_compat.py
#   3. Remove A2ACompatMiddleware from core/middlewares/__init__.py and factory._add_extra_middlewares()
#   4. Remove AionA2AFastAPIApplication._create_response() from core/app/a2a_fastapi.py
#   5. Remove AionA2AFastAPIApplication._handle_get_agent_card() from core/app/a2a_fastapi.py
#   6. Remove AION_A2A_COMPAT_ENABLED from environment / deployment configs

from .v03_to_v1 import A2AV1Adapter
from .v1_to_v03 import A2AV03Adapter

__all__ = ["A2AV1Adapter", "A2AV03Adapter"]
