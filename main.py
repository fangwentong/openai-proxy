#!/usr/bin/env python3
import json
import time
from datetime import datetime

import httpx
from fastapi import FastAPI, Request, HTTPException
from starlette.background import BackgroundTask

from log import OpenAILog, save_log
from utils import PathMatchingTree, OverrideStreamResponse

proxied_hosts = PathMatchingTree({
    "/": "https://api.openai.com",
    "/backend-api/conversation": "https://chat.openai.com",
})

# FastAPI app
app = FastAPI()


async def proxy_openai_api(request: Request):
    # proxy request to OpenAI API
    headers = {k: v for k, v in request.headers.items() if
               k not in {'host', 'content-length', 'x-forwarded-for', 'x-real-ip', 'connection'}}
    url = f'{proxied_hosts.get_matching(request.url.path)}{request.url.path}'

    start_time = datetime.now().microsecond
    # create httpx async client
    client = httpx.AsyncClient()

    request_body = await request.json() if request.method in {'POST', 'PUT'} else None

    log = OpenAILog()

    async def stream_api_response():
        nonlocal log
        try:
            st = client.stream(request.method, url, headers=headers, params=request.query_params, json=request_body)
            async with st as res:
                response.status_code = res.status_code
                response.init_headers({k: v for k, v in res.headers.items() if
                                       k not in {'content-length', 'content-encoding', 'alt-svc'}})

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


@app.route('/{path:path}', methods=['GET', 'POST', 'PUT', 'DELETE'])
async def request_handler(request: Request):
    return await proxy_openai_api(request)


if __name__ == '__main__':
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, log_level="info", reload=True)
