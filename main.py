#!/usr/bin/env python3
import time
from functools import partial

import typing
from fastapi import FastAPI, Request, HTTPException
import httpx
from sqlalchemy import create_engine, Column, Integer, String, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import databases
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.types import Send, Scope, Receive
import anyio
from datetime import datetime
import json
from utils import PathMatchingTree

# database config
DATABASE_URL = 'sqlite:///./openai_log.db'
database = databases.Database(DATABASE_URL)
Base = declarative_base()

proxied_hosts = PathMatchingTree({
    "/": "https://api.openai.com",
    "/backend-api/conversation": "https://chat.openai.com",
})


# database model
class OpenAILog(Base):
    """
    OpenAI API call log
    """
    __tablename__ = 'openai_logs'

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    request_url = Column(String)
    request_method = Column(String)
    request_time = Column(BigInteger)
    response_time = Column(BigInteger)
    status_code = Column(Integer)
    request_content = Column(String)
    response_header = Column(String)
    response_content = Column(String)

    def to_dict(self):
        return {
            'id': self.id,
            'request_url': self.request_url,
            'request_method': self.request_method,
            'request_time': self.request_time,
            'response_time': self.response_time,
            'status_code': self.status_code,
            'request_content': self.request_content,
            'response_header': self.response_header,
            'response_content': self.response_content,
        }


engine = create_engine(DATABASE_URL)
Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# FastAPI app
app = FastAPI()


async def save_log(log: OpenAILog):
    async with database.transaction():
        query = OpenAILog.__table__.insert().values(**log.to_dict())
        await database.execute(query)


@app.middleware('http')
async def proxy_openai_api(request: Request, call_next):
    # proxy request to OpenAI API
    headers = {k: v for k, v in request.headers.items() if
               k not in ['host', 'content-length', 'x-forwarded-for', 'x-real-ip', 'connection']}
    url = f'{proxied_hosts.get_matching(request.url.path)}{request.url.path}'

    start_time = datetime.now().microsecond
    # create httpx async client
    client = httpx.AsyncClient()

    request_body = await request.json() if request.method == 'POST' else None

    log = OpenAILog()

    async def stream_api_response():
        nonlocal log
        try:
            st = client.stream(request.method, url, headers=headers, params=request.query_params, json=request_body)
            async with st as res:
                response.status_code = res.status_code
                response.init_headers(res.headers)

                content = bytearray()
                async for chunk in res.aiter_bytes():
                    yield chunk
                    content.extend(chunk)

                # gather log data
                log.request_url = url
                log.request_method = request.method
                log.request_time = start_time
                log.response_time = time.time() - start_time
                log.status_code = res.status_code
                log.request_content = (await request.body()).decode('utf-8') if request.method == 'POST' else None
                log.response_content = content.decode('utf-8')
                log.response_header = json.dumps([[k, v] for k, v in res.headers.items()])

        except httpx.RequestError as exc:
            raise HTTPException(status_code=500, detail=f'An error occurred while requesting: {exc}')

    async def update_log():
        nonlocal log
        log.response_time = datetime.now().microsecond - start_time
        await save_log(log)

    response = OverrideStreamResponse(stream_api_response(), background=BackgroundTask(update_log))
    return response

    # noinspection PyUnreachableCode
    @app.route('/{path:path}', methods=['GET', 'POST', 'DELETE'])
    async def get_proxy(request: Request, path: str):
        return await call_next(request)


class OverrideStreamResponse(StreamingResponse):
    """
    Override StreamingResponse to support lazy send response status_code and response headers
    """

    async def stream_response(self, send: Send) -> None:
        first_chunk = True
        async for chunk in self.body_iterator:
            if first_chunk:
                await self.send_request_header(send)
                first_chunk = False
            if not isinstance(chunk, bytes):
                chunk = chunk.encode(self.charset)
            await send({'type': 'http.response.body', 'body': chunk, 'more_body': True})

        if first_chunk:
            await self.send_request_header(send)
            first_chunk = False
        await send({'type': 'http.response.body', 'body': b'', 'more_body': False})

    async def send_request_header(self, send: Send) -> None:
        await send(
            {
                'type': 'http.response.start',
                'status': self.status_code,
                'headers': self.raw_headers,
            }
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async with anyio.create_task_group() as task_group:
            async def wrap(func: typing.Callable[[], typing.Coroutine]) -> None:
                await func()
                await task_group.cancel_scope.cancel()

            task_group.start_soon(wrap, partial(self.stream_response, send))
            await wrap(partial(self.listen_for_disconnect, receive))

        if self.background is not None:
            await self.background()


if __name__ == '__main__':
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, log_level="info", reload=True)
