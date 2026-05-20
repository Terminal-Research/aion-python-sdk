from __future__ import annotations

from typing import TYPE_CHECKING

from a2a.utils import AGENT_CARD_WELL_KNOWN_PATH

from .proxy_utils import format_agent_proxy_path

if TYPE_CHECKING:
    from .port_manager import AionPortManager

PROXY_HOST = "http://localhost"


def generate_welcome_message(port_manager: AionPortManager) -> str:
    """Return an ASCII welcome banner for the AION system.

    Args:
        port_manager: AionPortManager instance with reserved ports

    Returns:
        A formatted multi-line string containing usage information.
    """
    if not port_manager:
        raise ValueError("port_manager is required")

    # Get proxy port (always active now)
    proxy_port = port_manager.get_proxy_port()
    if proxy_port:
        proxy_url = f"{PROXY_HOST}:{proxy_port}"
        proxy_info = f"- Proxy API: {proxy_url}"
    else:
        proxy_url = None
        proxy_info = "- Proxy: Not started"

    # Build agents information from port_manager
    agent_ports = port_manager.get_all_agent_ports()

    if not agent_ports:
        agents_info = "- Agents: No agents running"
    else:
        agents_info = "- Agents:"

        for agent_id, agent_port in agent_ports.items():
            agent_url = f"{PROXY_HOST}:{agent_port}"
            agents_info += f"\n  * {agent_id}:"

            # Agent card endpoints
            card_path = AGENT_CARD_WELL_KNOWN_PATH.lstrip("/")
            agents_info += f"\n    - Card: {agent_url}/{card_path}"
            if proxy_url:
                proxy_card_path = format_agent_proxy_path(agent_id, AGENT_CARD_WELL_KNOWN_PATH)
                agents_info += f"\n    - Card (Proxy): {proxy_url}{proxy_card_path}"

            # RPC endpoints
            agents_info += f"\n    - RPC: {agent_url}"
            if proxy_url:
                proxy_rpc_path = format_agent_proxy_path(agent_id)
                agents_info += f"\n    - RPC (Proxy): {proxy_url}{proxy_rpc_path}"

    return f"""

    Welcome to

╔═╗╦╔═╗╔╗╔  ╔═╗╔═╗╔═╗╔╗╔╔╦╗  ╔═╗╔═╗╦
╠═╣║║ ║║║║  ╠═╣║ ╦║╣ ║║║ ║   ╠═╣╠═╝║
╩ ╩╩╚═╝╝╚╝  ╩ ╩╚═╝╚═╝╝╚╝ ╩   ╩ ╩╩  ╩

{proxy_info}

{agents_info}
"""
