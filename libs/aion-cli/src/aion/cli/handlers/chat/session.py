"""Chat functionality for A2A client debugging"""
import json
import os
import urllib
from typing import Optional
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from aion.shared.types.a2a.enums import ArtifactId

_SKIP_ARTIFACT_IDS = frozenset({ArtifactId.STREAM_DELTA.value, ArtifactId.EPHEMERAL_MESSAGE.value})
from a2a.types import (
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from google.protobuf import json_format

def _to_json(msg) -> str:
    return json.dumps(json_format.MessageToDict(msg), ensure_ascii=False)


from .card_resolver import AionA2ACardResolver
from .utils import A2ARequestHelper
from ...utils.proxy_utils import format_agent_proxy_path


class ChatSession:
    """Handles A2A chat session logic"""

    def __init__(
            self,
            host: str,
            enabled_extensions: Optional[str] = None,
            custom_headers: Optional[dict] = None,
            use_push_notifications: bool = False,
            push_notification_receiver: str = 'http://localhost:5000',
            agent_id: Optional[str] = None,
            request_helper: Optional[A2ARequestHelper] = None,
            session_id: int = 0,
            no_stream: bool = False,
    ):
        self.host = host
        self.enabled_extensions = enabled_extensions
        self.custom_headers = custom_headers or {}
        self.use_push_notifications = use_push_notifications
        self.push_notification_receiver = push_notification_receiver
        self.agent_id = agent_id
        self.request_helper = request_helper or A2ARequestHelper()
        self.context_id = session_id if session_id > 0 else 0
        self.no_stream = no_stream

        # Setup headers
        self.headers = {}

        # Add extension headers if provided
        if enabled_extensions:
            ext_list = [
                ext.strip() for ext in enabled_extensions.split(',') if ext.strip()
            ]
            if ext_list:
                self.headers["X-A2A-Extension"] = ', '.join(ext_list)

        # Add custom headers
        self.headers.update(self.custom_headers)

    async def start(self, show_history: bool = False):
        """Start the chat session"""
        print(f'Will use headers: {self.headers}')
        if not self.context_id:
            self.context_id = uuid4().hex

        async with httpx.AsyncClient(timeout=30, headers=self.headers) as httpx_client:
            # Get agent card
            card_resolver = AionA2ACardResolver(
                httpx_client=httpx_client,
                base_url=self.host,
                agent_id=self.agent_id)
            card = await card_resolver.get_agent_card()

            print('======= Agent Card ========')
            print(_to_json(card))

            # Setup push notifications if needed
            if self.use_push_notifications:
                await self._setup_push_notifications()

            client_url = None
            if self.agent_id:
                client_url = "{host}{path}".format(host=self.host, path=format_agent_proxy_path(self.agent_id))

            factory = ClientFactory(
                config=ClientConfig(
                    httpx_client=httpx_client,
                    streaming=not self.no_stream,
                ),
            )
            client = factory.create(card)
            streaming = card.capabilities.streaming and not self.no_stream

            # Main chat loop
            continue_loop = True
            while continue_loop:
                print('=========  starting a new task ======== ')
                continue_loop, task_id = await self._complete_task(
                    client,
                    streaming,
                    None,
                )

                if show_history and continue_loop and task_id:
                    await self._show_task_history(client, task_id)

    async def _setup_push_notifications(self):
        """Setup push notification listener"""
        notif_receiver_parsed = urllib.parse.urlparse(
            self.push_notification_receiver
        )
        notification_receiver_host = notif_receiver_parsed.hostname
        notification_receiver_port = notif_receiver_parsed.port

        from .push_notification_listener import PushNotificationListener
        push_notification_listener = PushNotificationListener(
            host=notification_receiver_host,
            port=notification_receiver_port,
        )
        push_notification_listener.start()

    async def _complete_task(
            self,
            client,
            streaming: bool,
            task_id: Optional[str],
    ):
        """Collect input for one task and send it to the agent.

        Args:
            client: A2A client instance used to send the message.
            streaming: Whether the server supports streaming responses.
            task_id: Optional task identifier to continue.

        Returns:
            A tuple containing a flag indicating whether the chat loop should
            continue and the current task identifier.
        """
        # Get user input
        try:
            prompt = input('\nWhat do you want to send to the agent? (:q or quit to exit): ')
        except (EOFError, KeyboardInterrupt):
            return False, None

        if prompt.lower() in [':q', 'quit']:
            return False, None

        # Create message
        message = Message(
            role=Role.ROLE_USER,
            parts=[Part(text=prompt)],
            message_id=str(uuid4()),
            task_id=task_id,
            context_id=self.context_id,
        )

        attachment = self._prompt_attachment_part()
        if attachment is not None:
            message.parts.append(attachment)

        request = SendMessageRequest(
            message=message,
            configuration=SendMessageConfiguration(
                accepted_output_modes=['text'],
            ),
            metadata=self.request_helper.generate_task_metadata(),
        )

        return await self._handle_response(client, request, task_id)

    def _prompt_attachment_part(self) -> Optional[Part]:
        """Prompt for an attachment until the user skips or selects a valid input.

        Returns:
            A part ready to be attached to the outgoing message, or ``None``
            when the user chooses to continue without an attachment.
        """
        while True:
            file_path = input(
                'Select a file path or http/https URL to attach? (press enter to skip): '
            ).strip()
            if not file_path:
                return None

            url_part = self._build_remote_attachment_part(file_path)
            if url_part is not None:
                return url_part

            try:
                with open(file_path, 'rb') as file_obj:
                    file_content = file_obj.read()
            except FileNotFoundError:
                print(f"File not found: {file_path}")
                continue
            except Exception as error:
                print(f"Error reading file: {error}")
                continue

            return Part(raw=file_content, filename=os.path.basename(file_path))

    def _build_remote_attachment_part(self, file_path: str) -> Optional[Part]:
        """Build an attachment part for supported remote URLs.

        Args:
            file_path: Raw user input from the attachment prompt.

        Returns:
            A URL-based attachment part when the input is a supported remote
            resource; otherwise ``None``.
        """
        parsed_path = urllib.parse.urlparse(file_path)
        if parsed_path.scheme not in {"http", "https"}:
            return None

        filename = os.path.basename(urllib.parse.unquote(parsed_path.path)) or None
        return Part(url=file_path, filename=filename)

    async def _handle_response(
            self,
            client,
            request: SendMessageRequest,
            task_id: Optional[str]
    ):
        """Handle response from send_message (works for both streaming and non-streaming)"""
        task_result = None
        message = None
        had_stream_events = False

        try:
            async for stream_response, task in client.send_message(request):
                if stream_response.HasField('task'):
                    task_result = stream_response.task
                    task_id = task_result.id
                elif stream_response.HasField('message'):
                    message = stream_response.message
                    if message.context_id:
                        self.context_id = message.context_id
                elif stream_response.HasField('status_update'):
                    had_stream_events = True
                    event = stream_response.status_update
                    task_id = event.task_id
                    if event.context_id:
                        self.context_id = event.context_id
                    print(f'stream event => {_to_json(event)}')
                elif stream_response.HasField('artifact_update'):
                    had_stream_events = True
                    event = stream_response.artifact_update
                    task_id = event.task_id
                    print(f'stream event => {_to_json(event)}')

        except httpx.TimeoutException as e:
            print(f'Request timed out: {str(e)}. Continuing to next input.')
            return True, task_id

        except httpx.ConnectError as e:
            print(f'Connection failed: {str(e)}. Please check if the server is running.')
            return False, task_id

        except Exception as e:
            print(f'Unexpected error: {e}')
            return False, task_id

        return await self._process_task_result(
            message, task_result, client, task_id, streaming=had_stream_events
        )

    async def _process_task_result(
            self,
            message: Optional[Message],
            task_result: Optional[Task],
            client,
            task_id: Optional[str],
            streaming: bool = False
    ):
        """Process the final task result"""
        if message:
            print(f'\n{_to_json(message)}')
            return True, task_id

        if task_result:
            # Filter out ephemeral streaming artifacts (not persisted on server)
            visible_artifacts = [
                a for a in task_result.artifacts
                if a.artifact_id not in _SKIP_ARTIFACT_IDS
            ]
            del task_result.artifacts[:]
            task_result.artifacts.extend(visible_artifacts)
            print(f'\n{_to_json(task_result)}')

            # Check if more input is required
            if task_result.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
                return await self._complete_task(
                    client,
                    streaming,
                    task_id,
                )

            return True, task_id

        # Fallback case
        return True, task_id

    async def _get_task_result(self, client, task_id: str):
        """Get task result by ID with improved error handling"""
        try:
            task = await client.get_task(
                GetTaskRequest(id=task_id)
            )
            return task

        except httpx.TimeoutException:
            print('Timeout while getting task result')
            return None

        except httpx.ConnectError:
            print('Connection failed while getting task result')
            return None

        except Exception as e:
            print(f'Unexpected error getting task result: {e}')
            return None

    async def _show_task_history(self, client, task_id: str):
        """Show task history with error handling"""
        try:
            print('========= history ======== ')
            task = await client.get_task(
                GetTaskRequest(id=task_id, history_length=10)
            )
            print(_to_json(task))
        except Exception as e:
            print(f'Failed to get task history: {e}')


async def start_chat(
        host: str = 'http://localhost:8083',
        session_id: int = 0,
        show_history: bool = False,
        use_push_notifications: bool = False,
        push_notification_receiver: str = 'http://localhost:5000',
        enabled_extensions: Optional[str] = None,
        custom_headers: Optional[dict] = None,
        agent_id: Optional[str] = None,
        no_stream: bool = False,
):
    """Start a chat session with the A2A agent"""
    chat_session = ChatSession(
        host=host,
        enabled_extensions=enabled_extensions,
        custom_headers=custom_headers,
        use_push_notifications=use_push_notifications,
        push_notification_receiver=push_notification_receiver,
        agent_id=agent_id,
        session_id=session_id,
        no_stream=no_stream,
    )

    await chat_session.start(show_history=show_history)
