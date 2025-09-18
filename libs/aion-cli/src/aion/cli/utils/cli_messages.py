from aion.shared.aion_config.models import AionConfig, AgentConfig
from aion.shared.aion_config.reader import AionConfigReader


def welcome_message(aion_config: AionConfig, proxy_enabled: bool = True) -> str:
    """Return an ASCII welcome banner for the AION system.

    Args:
        aion_config: The AION configuration containing agents and proxy settings
        proxy_enabled: Whether the proxy server is enabled

    Returns:
        A formatted multi-line string containing usage information.
    """
    try:
        config = aion_config or AionConfigReader().load_and_validate_config()
    except Exception:
        config = None

    # Build proxy URL
    if proxy_enabled and config and config.proxy:
        proxy_url = f"http://localhost:{config.proxy.port}"
        proxy_info = f"- Proxy API: {proxy_url}"
    else:
        proxy_url = None
        proxy_info = "- Proxy: Disabled"

    # Build agents information
    if not config or not config.agents:
        agents_info = "- Agents: No agents configured"
    else:
        agents_info = "- Agents:"

        for agent_id, agent_config in config.agents.items():
            agent_url = f"http://localhost:{agent_config.port}"
            agents_info += f"\n  * {agent_id}:"

            agents_info += f"\n    - Card: {agent_url}/.well-known/agent-card.json"
            # Agent card endpoints
            if proxy_enabled and proxy_url:
                agents_info += f"\n    - Card (Proxy): {proxy_url}/{agent_id}/.well-known/agent-card.json"

            # RPC endpoints
            agents_info += f"\n    - RPC: {agent_url}"
            if proxy_enabled and proxy_url:
                agents_info += f"\n    - RPC (Proxy): {proxy_url}/{agent_id}/"

    return f"""

    Welcome to

╔═╗╦╔═╗╔╗╔  ╔═╗╔═╗╔═╗╔╗╔╔╦╗  ╔═╗╔═╗╦
╠═╣║║ ║║║║  ╠═╣║ ╦║╣ ║║║ ║   ╠═╣╠═╝║
╩ ╩╩╚═╝╝╚╝  ╩ ╩╚═╝╚═╝╝╚╝ ╩   ╩ ╩╩  ╩

{proxy_info}

{agents_info}
"""
