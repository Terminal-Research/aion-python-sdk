import asyncio
import threading

from fastapi import FastAPI, Request, Query, Response


class PushNotificationListener:
    def __init__(
        self,
        host,
        port,
    ):
        self.host = host
        self.port = port
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=lambda loop: loop.run_forever(), args=(self.loop,)
        )
        self.thread.daemon = True
        self.thread.start()

    def start(self):
        try:
            # Need to start server in separate thread as current thread
            # will be blocked when it is waiting on user prompt.
            asyncio.run_coroutine_threadsafe(
                self.start_server(),
                self.loop,
            )
            print('======= push notification listener started =======')
        except Exception as e:
            print(e)

    async def start_server(self):
        import uvicorn

        self.app = FastAPI()

        # Register routes
        self.app.add_api_route(
            '/notify',
            self.handle_validation_check,
            methods=['GET'],
            response_class=Response
        )
        self.app.add_api_route(
            '/notify',
            self.handle_notification,
            methods=['POST'],
            response_class=Response
        )

        config = uvicorn.Config(
            self.app, host=self.host, port=self.port, log_level='critical'
        )
        self.server = uvicorn.Server(config)
        await self.server.serve()

    async def handle_validation_check(
        self,
        validation_token: str = Query(None, alias="validationToken")
    ) -> Response:
        """Handle Teams channel validation.

        Args:
            validation_token: Validation token from Teams (received as validationToken in query params)

        Returns:
            Response with validation token or 400 error
        """
        print(
            f'\npush notification verification received => \n{validation_token}\n'
        )

        if not validation_token:
            return Response(status_code=400)

        return Response(content=validation_token, status_code=200)

    async def handle_notification(self, request: Request) -> Response:
        """Handle push notifications.

        Args:
            request: HTTP request with notification data

        Returns:
            Response with 200 status
        """
        data = await request.json()
        print(f'\npush notification received => \n{data}\n')
        return Response(status_code=200)
