from .env_settings import settings


class AionApiSettings:
    """
    Configuration class for Aion API connection settings.

    This class manages HTTP and WebSocket connection parameters for the Aion API.
    It provides lazy-loaded URL properties for both HTTP and WebSocket connections.

    Attributes:
        client_id (str): Client identifier for API authentication
        client_secret (str): Client secret for API authentication
        protocol (str): Connection protocol (http/https)
        host (str): API host address
        port (int): Connection port number
        keepalive (int): WebSocket keepalive interval in seconds
    """
    def __init__(self):
        """
        Initialize AionApiSettings with values from environment settings.

        Loads configuration from the settings module, applying default values
        for missing parameters.
        """
        self.client_id = settings.client_id
        self.client_secret = settings.client_secret
        self.protocol = settings.aion_api.get("protocol", "https")
        self.host = settings.aion_api.get("host", "api.aion.to")
        self.port = settings.aion_api.get("port", 443)
        self.keepalive = settings.aion_api.get("keepalive", 60)

    @property
    def http_url(self) -> str:
        """
        Get the complete HTTP URL for API requests.

        Constructs and caches the HTTP URL based on protocol, host, and port.
        Omits port number for standard ports (80 for HTTP, 443 for HTTPS).

        Returns:
            str: Complete HTTP URL for API requests
        """
        if hasattr(self, "_http_url"):
            return self._http_url

        default_ports = {"http": 80, "https": 443}
        if self.port in default_ports.values():
            url = f"{self.protocol}://{self.host}"
        else:
            url = f"{self.protocol}://{self.host}:{self.port}"

        self._http_url = url
        return self._http_url

    @property
    def gql_url(self) -> str:
        """
        Get the complete HTTP URL for GraphQL endpoint.

        Constructs and caches the GraphQL HTTP URL by appending the GraphQL
        WebSocket path to the base HTTP URL.

        Returns:
            str: Complete HTTP URL for GraphQL endpoint (/ws/graphql)
        """
        if hasattr(self, "_gql_url"):
            return self._gql_url

        self._gql_url = f"{self.http_url}/ws/graphql"
        return self._gql_url

    @property
    def ws_gql_url(self) -> str:
        """
        Get the complete WebSocket URL for GraphQL subscriptions.

        Constructs and caches the WebSocket URL with appropriate protocol
        (ws for HTTP, wss for HTTPS) and GraphQL endpoint path.

        Returns:
            str: Complete WebSocket URL for GraphQL subscriptions
        """
        if hasattr(self, "_ws_gql_url"):
            return self._ws_gql_url

        prefix = "wss" if self.protocol == "https" else "ws"
        self._ws_gql_url = f"{prefix}://{self.host}/ws/graphql"
        return self._ws_gql_url


aion_api_settings = AionApiSettings()
