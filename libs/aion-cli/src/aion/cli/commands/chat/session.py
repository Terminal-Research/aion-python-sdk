"""Chat functionality for A2A client debugging"""
import base64
import os
import urllib
from typing import Optional
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    FilePart,
    FileWithBytes,
    GetTaskRequest,
    JSONRPCErrorResponse,
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    Part,
    SendMessageRequest,
    SendStreamingMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskQueryParams,
    TaskState,
    TaskStatusUpdateEvent,
    TextPart,
)


class ChatSession:
    """Handles A2A chat session logic"""

    def __init__(
            self,
            agent_url: str,
            bearer_token: Optional[str] = None,
            enabled_extensions: Optional[str] = None,
            custom_headers: Optional[dict] = None,
            use_push_notifications: bool = False,
            push_notification_receiver: str = 'http://localhost:5000',
    ):
        self.agent_url = agent_url
        self.bearer_token = bearer_token
        self.enabled_extensions = enabled_extensions
        self.custom_headers = custom_headers or {}
        self.use_push_notifications = use_push_notifications
        self.push_notification_receiver = push_notification_receiver

        # Setup headers
        self.headers = {}
        if bearer_token:
            self.headers['Authorization'] = f'Bearer {bearer_token}'

        # Add extension headers if provided
        if enabled_extensions:
            ext_list = [
                ext.strip() for ext in enabled_extensions.split(',') if ext.strip()
            ]
            if ext_list:
                self.headers["X-A2A-Extension"] = ', '.join(ext_list)

        # Add custom headers
        self.headers.update(self.custom_headers)

    async def start(self, session_id: int = 0, show_history: bool = False):
        """Start the chat session"""
        print(f'Will use headers: {self.headers}')

        async with httpx.AsyncClient(timeout=30, headers=self.headers) as httpx_client:
            # Get agent card
            card_resolver = A2ACardResolver(httpx_client, self.agent_url)
            card = await card_resolver.get_agent_card()

            print('======= Agent Card ========')
            print(card.model_dump_json(exclude_none=True))

            # Setup push notifications if needed
            if self.use_push_notifications:
                await self._setup_push_notifications()

            client = A2AClient(httpx_client, agent_card=card)
            streaming = card.capabilities.streaming
            context_id = session_id if session_id > 0 else uuid4().hex

            # Main chat loop
            continue_loop = True
            while continue_loop:
                print('=========  starting a new task ======== ')
                continue_loop, _, task_id = await self._complete_task(
                    client,
                    streaming,
                    None,
                    context_id,
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
            client: A2AClient,
            streaming: bool,
            task_id: Optional[str],
            context_id: str,
    ):
        """Complete a single chat task"""
        # Get user input
        try:
            prompt = input('\nWhat do you want to send to the agent? (:q or quit to exit): ')
        except (EOFError, KeyboardInterrupt):
            return False, None, None

        if prompt.lower() in [':q', 'quit']:
            return False, None, None

        # Create message
        message = Message(
            role='user',
            parts=[TextPart(text=prompt)],
            message_id=str(uuid4()),
            task_id=task_id,
            context_id=context_id,
        )

        # Handle file attachment
        file_path = input('Select a file path to attach? (press enter to skip): ').strip()
        if file_path:
            try:
                with open(file_path, 'rb') as f:
                    file_content = base64.b64encode(f.read()).decode('utf-8')
                    file_name = os.path.basename(file_path)

                message.parts.append(
                    Part(
                        root=FilePart(
                            file=FileWithBytes(name=file_name, bytes=file_content)
                        )
                    )
                )
            except FileNotFoundError:
                print(f"File not found: {file_path}")
            except Exception as e:
                print(f"Error reading file: {e}")

        # Create payload
        payload = MessageSendParams(
            id=str(uuid4()),
            message=message,
            configuration=MessageSendConfiguration(
                accepted_output_modes=['text'],
            ),
        )

        # Add push notification config if enabled
        if self.use_push_notifications:
            notif_receiver_parsed = urllib.parse.urlparse(
                self.push_notification_receiver
            )
            payload['pushNotification'] = {
                'url': f'http://{notif_receiver_parsed.hostname}:{notif_receiver_parsed.port}/notify',
                'authentication': {
                    'schemes': ['bearer'],
                },
            }

        # Send message and handle response
        if streaming:
            return await self._handle_streaming_response(client, payload, context_id, task_id)
        else:
            return await self._handle_non_streaming_response(client, payload, context_id, task_id)

    async def _handle_streaming_response(
            self,
            client: A2AClient,
            payload: MessageSendParams,
            context_id: str,
            task_id: Optional[str]
    ):
        """Handle streaming response"""
        response_stream = client.send_message_streaming(
            SendStreamingMessageRequest(
                id=str(uuid4()),
                params=payload,
            )
        )

        task_result = None
        message = None
        task_completed = False

        async for result in response_stream:
            if isinstance(result.root, JSONRPCErrorResponse):
                print(f'Error: {result.root.error}, context_id: {context_id}, task_id: {task_id}')
                return False, context_id, task_id

            event = result.root.result
            context_id = event.context_id

            if isinstance(event, Task):
                task_id = event.id
            elif isinstance(event, (TaskStatusUpdateEvent, TaskArtifactUpdateEvent)):
                task_id = event.task_id
                if (isinstance(event, TaskStatusUpdateEvent) and
                        event.status.state == 'completed'):
                    task_completed = True
            elif isinstance(event, Message):
                message = event

            print(f'stream event => {event.model_dump_json(exclude_none=True)}')

        # Get full task if needed
        if task_id and not task_completed:
            task_result = await self._get_task_result(client, task_id, context_id)
            if task_result is None:
                return False, context_id, task_id

        return await self._process_task_result(
            message, task_result, client, context_id, task_id
        )

    async def _handle_non_streaming_response(
            self,
            client: A2AClient,
            payload: MessageSendParams,
            context_id: str,
            task_id: Optional[str]
    ):
        """Handle non-streaming response"""
        try:
            event = await client.send_message(
                SendMessageRequest(
                    id=str(uuid4()),
                    params=payload,
                )
            )
            event = event.root.result
        except Exception as e:
            print('Failed to complete the call', e)
            return False, context_id, task_id

        if not context_id:
            context_id = event.context_id

        task_result = None
        message = None

        if isinstance(event, Task):
            if not task_id:
                task_id = event.id
            task_result = event
        elif isinstance(event, Message):
            message = event

        return await self._process_task_result(
            message, task_result, client, context_id, task_id
        )

    async def _process_task_result(
            self,
            message: Optional[Message],
            task_result: Optional[Task],
            client: A2AClient,
            context_id: str,
            task_id: Optional[str]
    ):
        """Process the final task result"""
        if message:
            print(f'\n{message.model_dump_json(exclude_none=True)}')
            return True, context_id, task_id

        if task_result:
            # Print task content (excluding file contents)
            task_content = task_result.model_dump_json(
                exclude={
                    'history': {
                        '__all__': {
                            'parts': {
                                '__all__': {'file'},
                            },
                        },
                    },
                },
                exclude_none=True,
            )
            print(f'\n{task_content}')

            # Check if more input is required
            state = TaskState(task_result.status.state)
            if state.name == TaskState.input_required.name:
                return await self._complete_task(
                    client,
                    True,  # Assume streaming for recursive calls
                    task_id,
                    context_id,
                )

            return True, context_id, task_id

        # Fallback case
        return True, context_id, task_id

    async def _get_task_result(self, client: A2AClient, task_id: str, context_id: str):
        """Get task result by ID"""
        task_result_response = await client.get_task(
            GetTaskRequest(
                id=str(uuid4()),
                params=TaskQueryParams(id=task_id),
            )
        )

        if isinstance(task_result_response.root, JSONRPCErrorResponse):
            print(f'Error: {task_result_response.root.error}, context_id: {context_id}, task_id: {task_id}')
            return None

        return task_result_response.root.result

    async def _show_task_history(self, client: A2AClient, task_id: str):
        """Show task history"""
        print('========= history ======== ')
        task_response = await client.get_task(
            {'id': task_id, 'historyLength': 10}
        )
        print(
            task_response.model_dump_json(
                include={'result': {'history': True}}
            )
        )

async def start_chat(
        agent_url: str = 'http://localhost:8083',
        bearer_token: Optional[str] = None,
        session_id: int = 0,
        show_history: bool = False,
        use_push_notifications: bool = False,
        push_notification_receiver: str = 'http://localhost:5000',
        enabled_extensions: Optional[str] = None,
        custom_headers: Optional[dict] = None,
):
    """Start a chat session with the A2A agent"""
    chat_session = ChatSession(
        agent_url=agent_url,
        bearer_token=bearer_token,
        enabled_extensions=enabled_extensions,
        custom_headers=custom_headers,
        use_push_notifications=use_push_notifications,
        push_notification_receiver=push_notification_receiver,
    )

    await chat_session.start(session_id=session_id, show_history=show_history)
