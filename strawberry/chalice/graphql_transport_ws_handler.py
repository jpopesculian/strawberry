from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional, Dict
import json


from chalice.app import (
    BadRequestError,
    WebsocketAPI,
    WebsocketEvent,
    WebsocketDisconnectedError,
)
from strawberry.subscriptions import GRAPHQL_TRANSPORT_WS_PROTOCOL
from strawberry.subscriptions.protocols.graphql_transport_ws.handlers import (
    BaseGraphQLTransportWSHandler,
)
from .session_storage import SessionStorage

if TYPE_CHECKING:
    from datetime import timedelta

    from starlette.websockets import WebSocket

    from strawberry.schema import BaseSchema


class GraphQLTransportWSHandler(BaseGraphQLTransportWSHandler):
    def __init__(
        self,
        schema: BaseSchema,
        debug: bool,
        connection_init_wait_timeout: timedelta,
        get_context: Callable,
        get_root_value: Callable,
        websocket_api: WebsocketAPI,
        event: WebsocketEvent,
        session_storage: SessionStorage,
    ):
        super().__init__(schema, debug, connection_init_wait_timeout)
        self._get_context = get_context
        self._get_root_value = get_root_value
        self._websocket_api = websocket_api
        self._event = event
        self._session_storage = session_storage

    async def get_context(self) -> Any:
        return await self._get_context()

    async def get_root_value(self) -> Any:
        return await self._get_root_value()

    async def send_json(self, data: dict) -> None:
        self._websocket_api.send(
            connection_id=self._event.connection_id, message=json.dumps(data)
        )

    async def close(self, code: int, reason: str) -> None:
        await self.send_json(
            {"type": "websocket.close", "code": code, "reason": reason}
        )

    @property
    def connection_params(self) -> Optional[Dict[str, Any]]:
        return self._session_storage.get_connection_params(self._event.connection_id)

    @connection_params.setter
    def connection_params(self, connection_params: Optional[Dict[str, Any]]):
        if connection_params is not None:
            self._session_storage.set_connection_params(
                self._event.connection_id, connection_params, 60 * 60 * 24
            )

    @property
    def connection_acknowledged(self) -> bool:
        return self.connection_params is not None

    @connection_acknowledged.setter
    def connection_acknowledged(self, _: bool):
        pass

    async def handle_request(self) -> None:
        try:
            body = self._event.json_body
        except BadRequestError as _:
            return await self.handle_invalid_message("Invalid JSON payload")
        try:
            await self.handle_message(body)
        except WebsocketDisconnectedError as _:
            pass
