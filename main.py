import time
from fastapi import FastAPI, Request, HTTPException
import httpx
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import databases
from fastapi.responses import JSONResponse

# 数据库配置
DATABASE_URL = "sqlite:///./openai_log.db"
database = databases.Database(DATABASE_URL)
Base = declarative_base()


# 数据库模型
class OpenAILog(Base):
    __tablename__ = "openai_logs"

    id = Column(Integer, primary_key=True, index=True)
    request_url = Column(String)
    request_method = Column(String)
    request_time = Column(Float)
    response_time = Column(Float)
    status_code = Column(Integer)
    request_content = Column(String)
    response_content = Column(String)
    institution_id = Column(String)

    def to_dict(self):
        return {
            "id": self.id,
            "request_url": self.request_url,
            "request_method": self.request_method,
            "request_time": self.request_time,
            "response_time": self.response_time,
            "status_code": self.status_code,
            "request_content": self.request_content,
            "response_content": self.response_content,
            "institution_id": self.institution_id,
        }


engine = create_engine(DATABASE_URL)
Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# FastAPI 应用
app = FastAPI()


async def save_log(log: OpenAILog):
    async with database.transaction():
        query = OpenAILog.__table__.insert().values(**log.to_dict())
        await database.execute(query)


@app.middleware("http")
async def proxy_openai_api(request: Request, call_next):
    # if not request.query_params.get("institution_id"):
    #     raise HTTPException(status_code=403, detail="Missing institution_id in querystring")

    institution_id = request.query_params.get("institution_id")

    # 移除 institution_id
    query_params = {k: v for k, v in request.query_params.items() if k != 'institution_id'}

    # 转发请求到 OpenAI API
    headers = {"Authorization": request.headers.get("Authorization")}
    url = f"https://api.openai.com{request.url.path}"

    start_time = time.time()
    try:
        async with httpx.AsyncClient() as client:
            if request.method == "GET":
                response = await client.get(url, headers=headers, params=query_params)
            elif request.method == "POST":
                request_data = await request.json()  # 添加 await 关键字
                response = await client.post(url, headers=headers, params=query_params, json=request_data)
            else:
                raise HTTPException(status_code=405, detail="Method not allowed")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=500, detail=f"An error occurred while requesting: {exc}")

    end_time = time.time()
    # 记录日志
    log = OpenAILog(
        request_url=url,
        request_method=request.method,
        request_time=start_time,
        response_time=end_time - start_time,
        status_code=response.status_code,
        request_content=str(await request.body()) if request.method == "POST" else None,
        response_content=str(response.text),
        institution_id=institution_id,
    )
    await save_log(log)

    # 返回响应
    return JSONResponse(content=response.json(), status_code=response.status_code)

    @app.get("/{path:path}")
    async def get_proxy(request: Request, path: str):
        return await call_next(request)

    @app.post("/{path:path}")
    async def post_proxy(request: Request, path: str):
        return await call_next(request)
