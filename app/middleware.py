import json
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4
from app.errors import error_response

logger = logging.getLogger('marketplace.requests')
SENSITIVE = {'password','password_hash','access_token','refresh_token','token','authorization','secret','jwt_secret'}


def redact(value):
    if isinstance(value,dict):
        return {key:('***' if key.lower() in SENSITIVE else redact(item)) for key,item in value.items()}
    if isinstance(value,list):
        return [redact(item) for item in value]
    return value


class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope,receive,send)
        started = time.perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat()
        request_id = str(uuid4())
        state = scope.setdefault('state',{})
        state['request_id'] = request_id
        body = bytearray()
        truncated = False
        status_code = 500
        response_started = False

        async def logged_receive():
            nonlocal truncated
            message = await receive()
            if message['type'] == 'http.request':
                content = message.get('body',b'')
                if len(body) + len(content) <= 65536:
                    body.extend(content)
                else:
                    truncated = True
            return message

        async def logged_send(message):
            nonlocal status_code, response_started
            if message['type'] == 'http.response.start':
                status_code = message['status']
                response_started = True
                message['headers'] = [(key,val) for key,val in message.get('headers',[]) if key.lower() != b'x-request-id'] + [(b'x-request-id',request_id.encode())]
            await send(message)

        try:
            await self.app(scope,logged_receive,logged_send)
        except Exception as exception:
            logger.error(json.dumps({'event':'internal_error','request_id':request_id,'exception_type':type(exception).__name__}))
            if not response_started:
                await error_response('INTERNAL_ERROR',500,'Внутренняя ошибка сервера')(scope,receive,logged_send)
        finally:
            entry = {'request_id':request_id,'method':scope['method'],'endpoint':scope['path'],'status_code':status_code,'duration_ms':round((time.perf_counter()-started)*1000,3),'user_id':state.get('user_id'),'timestamp':timestamp}
            if scope['method'] in ('POST','PUT','DELETE'):
                try:
                    entry['request_body'] = {'truncated':True} if truncated else (redact(json.loads(body,parse_constant=lambda _: None)) if body else None)
                except (ValueError,UnicodeDecodeError):
                    entry['request_body'] = {'invalid_json':True}
            logger.info(json.dumps(entry,ensure_ascii=False,allow_nan=False))
