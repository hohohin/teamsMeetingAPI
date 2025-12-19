# main.py
import asyncio
import logging
import json
import httpx

from uuid import uuid4
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, status, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from sqlmodel import Session, select

# 导入自定义模块
from databacy import init_db, get_db, engine, Task, TaskCRUD, User, UserCreate, UserRead
# from database_user import create_db_and_tables, get_session
# from database_user import User, UserCreate, UserRead
# from models import LoginRequest

import server  # 你的阿里云交互代码
import aos


# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 环境变量
# TOS_AK = os.getenv("TOS_ACCESS_KEY")
# TOS_SK = os.getenv("TOS_SECRET_KEY")

# --- 辅助函数：TOS 配置映射 ---
def get_tos_config(region: str):
    match region:
        case "guangzhou":
            return "yings-meeting", "tos-cn-guangzhou.volces.com"
        case "hongkong":
            return "bucket4hk", "tos-cn-hongkong.volces.com"
        case _:
            raise ValueError(f"Unknown region: {region}")
        
async def jsonize_stt_url(url):
    try:
        # 使用异步上下文管理器发起请求
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            
            # 检查 HTTP 状态码，如果不是 200 则抛出异常
            response.raise_for_status()
            
            # 将响应内容解析为 JSON
            data = response.json()
            
            return data
            
    except httpx.HTTPStatusError as e:
        # 处理 HTTP 错误（例如 403 Forbidden, 404 Not Found）
        raise HTTPException(status_code=e.response.status_code, detail=f"Failed to fetch data: {str(e)}")
    except Exception as e:
        # 处理其他错误
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

# --- 数据库依赖 ---
# def get_db():
#     with Session(engine) as session:
#         yield session

# --- 核心：后台处理逻辑 ---

async def process_submission(db: Session):
    """
    阶段 1: 查找 status='NONE' 的记录 -> 构造URL -> 提交给阿里云 -> 更新为 'ONGOING'
    """
    crud = TaskCRUD(db)
    pending_tasks = crud.get_tasks_by_status("NONE")
    internal_client = aos.init_client(is_asycn=False, endpoint='custom') # Oss url
    
    for task in pending_tasks:
        try:
            logger.info(f"[Submit] Processing pending task: {task.object_key}")
            
            # 1. 构造 TOS URL
            # bucket, endpoint = get_tos_config(task.region)
            # 注意：如果是私有读Bucket，这里需要生成带签名的URL, 替换 URL 生成逻辑为：
            # client = tos.TosClientV2(TOS_AK, TOS_SK, endpoint, task.region)
            # file_url = client.generate_presigned_url("GET", bucket, task.object_key, expires=3600)

            # 这里假设是公共读或者Tingwu服务器有权限访问
            # file_url = f"https://{bucket}.{endpoint}/{quote(task.object_key)}"

            
            # 2. 调用 Server 代码提交任务 (运行在线程池中以免阻塞)
            # 使用 task.id 作为 task_key，方便后续追踪
            res = await asyncio.to_thread(server.submit_task, internal_client, task.object_key)
            
            # 3. 更新数据库
            if res and res.get("task_id"):
                crud.update_task(
                    task, 
                    status="ONGOING", 
                    task_id=res["task_id"]
                )
                logger.info(f"[Submit] Submitted {task.object_key}, Task ID: {res['task_id']}")
            else:
                logger.error(f"[Submit] Failed to submit {task.object_key}: {res}")
                # 可选：增加重试计数，或者标记为 SUBMIT_FAILED
                
        except Exception as e:
            logger.error(f"[Submit] Error processing {task.object_key}: {e}")

async def process_polling(db: Session):
    """
    阶段 2: 查找 status='ONGOING' 的记录 -> 查询阿里云 -> 更新为 'COMPLETED'
    """
    crud = TaskCRUD(db)
    ongoing_tasks = crud.get_tasks_by_status("ONGOING")
    # logger.info(f"ongoing task found: {ongoing_tasks}")
    
    for task in ongoing_tasks:
        # 如果没有 task_id，说明提交阶段可能出错了，跳过
        if not task.task_id:
            continue
            
        try:
            # 1. 查询状态
            # logger.info(f"quering task{task.task_id}")
            res = await asyncio.to_thread(server.query_task, task.task_id)
            logger.info(f"res still ongoing: {res}")
            
            if not res or not hasattr(res, 'body') or not hasattr(res.body, 'data'):
                continue

            remote_status = res.body.data.task_status
            
            # 2. 根据状态更新
            if remote_status == "COMPLETED":
                result_data = res.body.data.result
                # 转换 result 对象为 dict
                result_dict = result_data.to_map() if hasattr(result_data, 'to_map') else result_data
                chapters_url = result_dict["AutoChapters"]
                summary_url = result_dict["Summarization"]
                transcripts_url = result_dict["Transcription"]

                chapters = await jsonize_stt_url(chapters_url)
                summary = await jsonize_stt_url(summary_url)
                transcripts = await jsonize_stt_url(transcripts_url)
                
                # update in database
                crud.update_task(task, status="COMPLETED", query_res=result_dict, chapters=chapters, summary=summary, transcripts=transcripts)
                logger.info(f"[Poll] Task {task.object_key} COMPLETED.")
                
            elif remote_status == "FAILED":
                crud.update_task(task, status="FAILED", query_res={"error": "AliCloud Task Failed"})
                logger.error(f"[Poll] Task {task.object_key} FAILED remotely.")
                
        except Exception as e:
            logger.error(f"[Poll] Error querying {task.task_id}: {e}")

async def background_worker():
    """后台主循环"""
    logger.info("Background worker started.")
    while True:
        try:
            with Session(engine) as db:
                # 1. 处理提交 (NONE -> ONGOING)
                await process_submission(db)
                
                # 2. 处理查询 (ONGOING -> COMPLETED)
                await process_polling(db)
                
            await asyncio.sleep(15) # 休息5秒
        except Exception as e:
            logger.error(f"Critical error in background worker: {e}")
        await asyncio.sleep(10)


# --- Authorization ---
# 1. 配置密码哈希上下文
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# 2. 定义 Token 的数据模型 (Pydantic Schema)
# from models import Token
# 关键点确认： pwd_context 是我们用来处理密码的工具。之后我们会用到它的两个方法：
# pwd_context.hash(password): 加密密码。
# pwd_context.verify(plain_password, hashed_password): 验证密码是否正确。

# 到这一步，后端的基础设施就搭好了。接下来我们要进入最核心的部分：编写生成 JWT 的逻辑。
# 为了生成 JWT，我们需要定义三个配置项：

# SECRET_KEY: 密钥（千万不能泄露）。
# ALGORITHM: 加密算法（通常用 HS256）。
# ACCESS_TOKEN_EXPIRE_MINUTES: Token 多久过期。


# --- FastAPI App ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动初始化
    init_db()
    # create_db_and_tables()
    worker_task = asyncio.create_task(background_worker())
    yield
    # 关闭清理
    worker_task.cancel()

app = FastAPI(lifespan=lifespan)

# --- CORS ---

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://ecmeetings.org","https://yapteamsmeeting.onrender.com","http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- API 路由 ---
## --- 认证页面 ---
fake_users_db = {
    "johndoe": {
        "username": "johndoe",
        # 这是 "secret" 的 Argon2 哈希值
        "hashed_password": "$argon2id$v=19$m=65536,t=3,p=4$b611zlkrBSBk7N17D4Fwjg$0fY7261hhH3/GT4Uh+5J0YM8Vfik8lYb/vjt4LfSuLU" 
    }
}

# 1. 专门用于从 Cookie 中提取 Token 的依赖项
from auth import create_access_token, get_current_user


# 2. 新增一个验证接口：只有登录用户才能调通
# 依赖注入 (get_current_user) 帮你拿到了复杂的 Python 对象。
# 响应模型 (response_model=User) 帮你把这个对象自动转换成了标准的 JSON 格式发给前端。
@app.get("/users/me", response_model=UserRead)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

@app.post("/token")
async def login_for_access_token(response: Response, form_data: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_db)):
# 这里有一个 FastAPI 的“冷知识”需要特别注意： 我们使用的 OAuth2PasswordRequestForm 是一个基于 OAuth2 标准的表单。这个标准规定，用户提交的“账号”字段名必须叫 username，哪怕实际上用户填的是邮箱或手机号。
    statement = select(User).where(User.agent_code == form_data.username)
    user = session.exec(statement).first()

    if not user or not pwd_context.verify(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = create_access_token(data={"sub": user.agent_code})
    
    # ✨ 魔法时刻：设置 httpOnly Cookie
    response.set_cookie(
        key="access_token",          # Cookie 的名字
        value=f"Bearer {access_token}", # Cookie 的值
        httponly=True,               # 🚫 关键！禁止 JavaScript 读取，防止 XSS
        max_age=1800,                # 30分钟后过期
        samesite="lax",              # 防止 CSRF 的一种策略
        secure=False                 # 本地开发用 False (HTTP)，上线用 HTTPS 时必须改为 True
    )
    
    return {"message": "Login successful"} # 返回简单的成功信息即可


@app.post("/logout")
async def logout(response: Response):
    # 让浏览器删除名为 access_token 的 Cookie
    response.delete_cookie(key="access_token") 
    return {"message": "Logged out successfully"}


@app.post("/users", response_model=UserRead)
def create_user(user_create: UserCreate, session: Session = Depends(get_db)):
    existing_user = session.exec(select(User).where(User.agent_code == user_create.agent_code)).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Code replicated")
    # 1. 这里的 user_create.password 是明文，我们需要把它加密
    hashed_password = pwd_context.hash(user_create.password)
    # 2. 创建数据库模型实例
    db_user = User.model_validate(user_create, update={"hashed_password": hashed_password})
    new_db_user = User(
        agent_code = user_create.agent_code,
        hashed_password = hashed_password
    )
    session.add(new_db_user)
    # print(f"1. Add 之後: {new_db_user in session}")
    session.commit()
    # print(f"2. Commit 之後: {new_db_user in session}")
    # print(f"3. 生成的 ID: {new_db_user.id}")
    session.refresh(db_user)
    return db_user

## --- 功能页面
@app.get("/api/files/")
async def get_files(db: Session = Depends(get_db)):
    """同步 OSS 文件列表到数据库"""
    crud = TaskCRUD(db)
    client = aos.init_client()
    try:
        result = await aos.get_all_files(client,'yaps-meeting')
        for item in result.contents:
            # 检查数据库是否存在
            record = crud.get_task_by_key(item.key)
            
            if record is None:
                # 发现新文件，插入数据库，状态设为 NONE (等待后台自动提交)
                new_task = {
                    "id": str(uuid4()),
                    "object_key": item.key,
                    "region": 'cn-hongkong',
                    "size": item.size,
                    "last_modified": item.last_modified.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "NONE"
                }
                crud.create_task(new_task)
                logger.info(f"Synced new file: {item.key}")
            else:
                # 更新已有文件信息
                crud.update_task(
                    record, 
                    size=item.size, 
                    last_modified=item.last_modified.strftime("%Y-%m-%d %H:%M:%S")
                )

        # 返回所有文件记录
            all_records = db.exec(select(Task)).all()
        # 注意：实际生产中这里应该分页，否则数据库大时会卡死
        return all_records

    except Exception as e:
        logger.error(f"Error syncing files: {e}")
        # raise HTTPException(status_code=500, detail=str(e)) turn on in bebug
        return []

@app.post("/api/upload/{region}")
async def upload_file(region: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传文件到 TOS 并写入数据库 (Status=NONE)"""
    return {"status":"function not ready yet"}
    
# 下载会议文件
@app.get("/api/download/{region}/{object_key}")
async def download_file(region:str, object_key: str):
    # 后续可添加属性：下载次数
    return

# 刪除記錄


# 详情页
@app.get("/api/meetings/{object_key}")
async def file_detail(object_key: str, db: Session = Depends(get_db)):
    crud = TaskCRUD(db)
    db_task = crud.get_task_by_key(object_key)
    
    if db_task is None:
        raise HTTPException(status_code=404, detail="Subtitle not found")
    
    client = aos.init_client(is_asycn=False, endpoint='custom')

    try:
        url = aos.get_object_url(client, object_key)
    except Exception as e:
        raise HTTPException(500, f"Error getting url: {e}")

    # 如果 query_res 是 JSON 字符串，可以反序列化为 dict（可选）
    result = {
        "id": db_task.id,
        "object_key": db_task.object_key,
        "region": db_task.region,
        "size": db_task.size,
        "task_id": db_task.task_id,
        "status": db_task.status,
        "query_res": json.loads(db_task.query_res) if db_task.query_res else {},
        "summary": json.loads(db_task.summary) if db_task.summary else {},
        "chapters": json.loads(db_task.chapters) if db_task.chapters else {},
        "transcripts": json.loads(db_task.transcripts) if db_task.transcripts else {},
        "url":url,
        "created_at": db_task.created_at,
        "last_modified": db_task.last_modified,
    }
    
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)