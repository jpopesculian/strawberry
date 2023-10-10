from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional, Union, cast, Sequence
from datetime import timedelta
import json

from chalice.app import Request, Response, WebsocketEvent, WebsocketAPI
from strawberry.http.exceptions import HTTPException
from strawberry.http.sync_base_view import SyncBaseHTTPView, SyncHTTPRequestAdapter
from strawberry.http.temporal_response import TemporalResponse
from strawberry.http.types import HTTPMethod, QueryParams
from strawberry.http.typevars import Context, RootValue
from strawberry.utils.graphiql import get_graphiql_html
from strawberry.subscriptions import GRAPHQL_TRANSPORT_WS_PROTOCOL, GRAPHQL_WS_PROTOCOL
from .graphql_transport_ws_handler import GraphQLTransportWSHandler
from .session_storage import SessionStorage

if TYPE_CHECKING:
    from strawberry.http import GraphQLHTTPResponse
    from strawberry.schema import BaseSchema

_WS_PROTOCOL_HEADER = "Sec-WebSocket-Protocol"


class ChaliceHTTPRequestAdapter(SyncHTTPRequestAdapter):
    def __init__(self, request: Request):
        self.request = request

    @property
    def query_params(self) -> QueryParams:
        return self.request.query_params or {}  # type: ignore

    @property
    def body(self) -> Union[str, bytes]:
        return self.request.raw_body

    @property
    def method(self) -> HTTPMethod:
        return cast(HTTPMethod, self.request.method.upper())

    @property
    def headers(self) -> Mapping[str, str]:
        return self.request.headers

    @property
    def post_data(self) -> Mapping[str, Union[str, bytes]]:
        raise NotImplementedError

    @property
    def files(self) -> Mapping[str, Any]:
        raise NotImplementedError

    @property
    def content_type(self) -> Optional[str]:
        return self.request.headers.get("Content-Type", None)


class GraphQLView(
    SyncBaseHTTPView[Request, Response, TemporalResponse, Context, RootValue]
):
    allow_queries_via_get: bool = True
    request_adapter_class = ChaliceHTTPRequestAdapter

    def __init__(
        self,
        schema: BaseSchema,
        graphiql: bool = True,
        debug: bool = False,
        allow_queries_via_get: bool = True,
        subscription_url: str = "",
        session_storage: Optional[SessionStorage] = None,
        subscription_protocols: Sequence[str] = (
            GRAPHQL_TRANSPORT_WS_PROTOCOL,
            GRAPHQL_WS_PROTOCOL,
        ),
    ):
        self.graphiql = graphiql
        self.allow_queries_via_get = allow_queries_via_get
        self.schema = schema
        self.debug = debug
        self.subscription_url = subscription_url
        self.session_storage = session_storage
        self.protocols = subscription_protocols

    def get_root_value(self, request: Request) -> Optional[RootValue]:
        return None

    def render_graphiql(self, request: Request) -> Response:
        """
        Returns a string containing the html for the graphiql webpage. It also caches
        the result using lru cache.
        This saves loading from disk each time it is invoked.

        Returns:
            The GraphiQL html page as a string
        """
        return Response(
            get_graphiql_html(subscription_url=self.subscription_url),
            headers={"Content-Type": "text/html"},
        )

    def get_sub_response(self, request: Request) -> TemporalResponse:
        return TemporalResponse()

    @staticmethod
    def error_response(
        message: str,
        error_code: str,
        http_status_code: int,
        headers: Optional[Dict[str, str]] = None,
    ) -> Response:
        """
        A wrapper for error responses
        Returns:
        An errors response
        """
        body = {"Code": error_code, "Message": message}

        return Response(body=body, status_code=http_status_code, headers=headers)

    def get_context(self, request: Request, response: TemporalResponse) -> Context:
        return {"request": request, "response": response}  # type: ignore

    def get_ws_context(self, event: WebsocketEvent) -> Context:
        return {"event": event}  # type: ignore

    def get_ws_root_value(self, event: WebsocketEvent) -> Optional[RootValue]:
        return None

    def create_response(
        self, response_data: GraphQLHTTPResponse, sub_response: TemporalResponse
    ) -> Response:
        status_code = 200

        if sub_response.status_code != 200:
            status_code = sub_response.status_code

        return Response(
            body=self.encode_json(response_data),
            status_code=status_code,
            headers=sub_response.headers,
        )

    def execute_request(self, request: Request) -> Response:
        try:
            return self.run(request=request)
        except HTTPException as e:
            error_code_map = {
                400: "BadRequestError",
                401: "UnauthorizedError",
                403: "ForbiddenError",
                404: "NotFoundError",
                409: "ConflictError",
                429: "TooManyRequestsError",
                500: "ChaliceViewError",
            }

            return self.error_response(
                error_code=error_code_map.get(e.status_code, "ChaliceViewError"),
                message=e.reason,
                http_status_code=e.status_code,
            )

    def handle_ws_open(self, event: WebsocketEvent) -> Response:
        if self.session_storage is None:
            raise ValueError("No session storage defined")

        protocols = (
            event.to_dict().get("headers", {}).get(_WS_PROTOCOL_HEADER, "").split(",")
        )
        intersection = set(protocols) & set(self.protocols)
        protocol = min(
            intersection,
            key=lambda i: protocols.index(i),
            default=None,
        )
        if protocol is None:
            return Response(
                body=f"Unsupported protocol: {protocols}; Supported: {self.protocols}",
                status_code=400,
            )
        self.session_storage.set_protocol(event.connection_id, protocol, 60 * 60 * 24)
        return Response(body=None, headers={_WS_PROTOCOL_HEADER: protocol})

    async def handle_ws_message(
        self, websocket_api: WebsocketAPI, event: WebsocketEvent
    ):
        if self.session_storage is None:
            raise ValueError("No session storage defined")

        protocol = self.session_storage.get_protocol(event.connection_id)

        # TODO support GRAPHQL_WS_PROTOCOL
        if protocol != GRAPHQL_TRANSPORT_WS_PROTOCOL:
            websocket_api.send(
                connection_id=event.connection_id,
                message=json.dumps(
                    {
                        "type": "websocket.close",
                        "code": 4406,
                        "reason": "Subprotocol not acceptable",
                    }
                ),
            )
            return

        def _get_context():
            return self.get_ws_context(event=event)

        def _get_root_value():
            return self.get_ws_root_value(event=event)

        await GraphQLTransportWSHandler(
            schema=self.schema,
            debug=self.debug,
            connection_init_wait_timeout=timedelta(),
            get_context=_get_context,
            get_root_value=_get_root_value,
            websocket_api=websocket_api,
            event=event,
            session_storage=self.session_storage,
        ).handle()
