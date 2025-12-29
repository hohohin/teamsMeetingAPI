# main.py
import os
import asyncio
import logging
import json
import httpx

from uuid import uuid4
from datetime import datetime
from contextlib import asynccontextmanager

# [修改] 引入 run_in_threadpool 用於解決 async 函數中執行同步 DB 操作導致的卡死問題
from fastapi.concurrency import run_in_threadpool
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, status, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from sqlmodel import Session, select

# 导入自定义模块
from databacy import init_db, get_db, engine, Task, TaskCRUD, User, UserCreate, UserRead
import server  # 你的阿里云交互代码
import aos
from auth import create_access_token, get_current_user

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 环境变量
IS_PRODUCTION = os.getenv("RENDER") is not None 

# --- 辅助函数 ---
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
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            return data
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Failed to fetch data: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")


# --- [修改] 核心：后台处理逻辑 (重构以避免死锁) ---

async def process_submission():
    """
    阶段 1: 查找 status='NONE' 的记录 -> 构造URL -> 提交给阿里云 -> 更新为 'ONGOING'
    [修改] 不再接收 db 參數，而是內部自行管理 Session，避免長時間佔用連接
    """
    tasks_to_process = []
    
    # 1. 快速獲取任務 (同步操作，放入線程池或快速執行)
    # 這裡使用 with Session 確保用完即關
    with Session(engine) as db:
        crud = TaskCRUD(db)
        pending_tasks = crud.get_tasks_by_status("NONE")
        # 提取需要的數據，脫離 Session 範圍
        tasks_to_process = [{"id": t.id, "object_key": t.object_key} for t in pending_tasks]
    
    if not tasks_to_process:
        return

    client = aos.init_client(is_async=False, endpoint='custom')
    total = len(tasks_to_process)
    
    for index, task_info in enumerate(tasks_to_process):
        object_key = task_info["object_key"]
        task_db_id = task_info["id"]
        
        try:
            logger.info(f"[Submit] Processing {index + 1}/{total}: {object_key}")
            # [修改] 網絡請求是耗時操作，確保不持有 DB 鎖
            res = await asyncio.to_thread(server.submit_task, client, object_key)
            
            # 2. 更新數據庫 (重新建立短連接)
            if res and res.get("task_id"):
                with Session(engine) as db:
                    crud = TaskCRUD(db)
                    # 重新獲取對象以附加到當前 Session
                    current_task = crud.get_task_by_key(object_key)
                    if current_task:
                        crud.update_task(
                            current_task, 
                            status="ONGOING", 
                            task_id=res["task_id"]
                        )
                logger.info(f"[Submit] Submitted {object_key}, Task ID: {res['task_id']}")
            else:
                logger.error(f"[Submit] Failed to submit {object_key}: {res}")
                
        except Exception as e:
            logger.error(f"[Submit] Error processing {object_key}: {e}")
        
        # 避免請求過於頻繁
        await asyncio.sleep(1)

async def process_polling():
    """
    阶段 2: 查找 status='ONGOING' 的记录 -> 查询阿里云 -> 更新为 'COMPLETED'
    [修改] 同樣採用短連接策略
    """
    tasks_to_check = []
    
    with Session(engine) as db:
        crud = TaskCRUD(db)
        ongoing_tasks = crud.get_tasks_by_status("ONGOING")
        # 提取數據
        tasks_to_check = [{"id": t.id, "task_id": t.task_id, "object_key": t.object_key} for t in ongoing_tasks]
    
    if not tasks_to_check:
        return

    for task_info in tasks_to_check:
        ali_task_id = task_info["task_id"]
        db_id = task_info["id"]
        object_key = task_info["object_key"]

        if not ali_task_id:
            continue
            
        try:
            # 1. 查詢狀態 (耗時網絡操作，無 DB 鎖)
            res = await asyncio.to_thread(server.query_task, ali_task_id)
            
            if not res or not hasattr(res, 'body') or not hasattr(res.body, 'data'):
                continue

            remote_status = res.body.data.task_status
            
            # 2. 根據狀態更新
            if remote_status == "COMPLETED":
                result_data = res.body.data.result
                result_dict = result_data.to_map() if hasattr(result_data, 'to_map') else result_data
                
                # 下載也是異步 IO 操作
                chapters = await jsonize_stt_url(result_dict["AutoChapters"])
                summary = await jsonize_stt_url(result_dict["Summarization"])
                transcripts = await jsonize_stt_url(result_dict["Transcription"])
                
                # 寫回數據庫
                with Session(engine) as db:
                    crud = TaskCRUD(db)
                    task_record = crud.get_task_by_key(object_key)
                    if task_record:
                        crud.update_task(task_record, status="COMPLETED", query_res=result_dict, chapters=chapters, summary=summary, transcripts=transcripts)
                logger.info(f"[Poll] Task {object_key} COMPLETED.")
                
            elif remote_status == "FAILED":
                with Session(engine) as db:
                    crud = TaskCRUD(db)
                    task_record = crud.get_task_by_key(object_key)
                    if task_record:
                        crud.update_task(task_record, status="FAILED", query_res={"error": "AliCloud Task Failed"})
                logger.error(f"[Poll] Task {object_key} FAILED remotely.")
                
        except Exception as e:
            logger.error(f"[Poll] Error querying {ali_task_id}: {e}")
            await asyncio.sleep(1) # 出錯時稍微等待

async def background_worker():
    """后台主循环"""
    logger.info("Background worker started.")
    while True:
        try:
            # [修改] 不要在這裡開啟全局 Session
            await process_submission()
            await process_polling()
        except Exception as e:
            logger.error(f"Critical error in background worker: {e}")
        
        # [修改] 必須等待，讓出 Event Loop 給 API 請求
        await asyncio.sleep(5)


# --- Authorization ---
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# --- FastAPI App ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动初始化
    init_db()
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

@app.get("/api/users/me", response_model=UserRead)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

# [修改] 改為同步函數 (def)，因為它主要依賴同步的 DB 操作
# FastAPI 會自動將其放入線程池，防止阻塞 Main Loop
@app.post("/api/token")
def login_for_access_token(response: Response, form_data: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_db)):
    statement = select(User).where(User.agent_code == form_data.username)
    user = session.exec(statement).first()

    if not user or not pwd_context.verify(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = create_access_token(data={"sub": user.agent_code})
    
    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=True,
        max_age=1800,
        samesite="none",
        secure=IS_PRODUCTION
    )
    
    return {"message": "Login successful"}


@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie(key="access_token") 
    return {"message": "Logged out successfully"}


@app.post("/api/users", response_model=UserRead)
def create_user(user_create: UserCreate, session: Session = Depends(get_db)):
    existing_user = session.exec(select(User).where(User.agent_code == user_create.agent_code)).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Code replicated")
    hashed_password = pwd_context.hash(user_create.password)
    db_user = User.model_validate(user_create, update={"hashed_password": hashed_password})
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    return db_user

## --- 功能页面 ---

# [修改] 保持 async def，因為用到了 await aos...
# 但必須使用 run_in_threadpool 處理同步 DB 邏輯
@app.get("/api/files")
async def get_files(db: Session = Depends(get_db)):
    """同步 OSS 文件列表到数据库"""
    client = aos.init_client()
    try:
        # 1. 異步獲取文件列表 (不會阻塞)
        dates, contents = await aos.get_all_files(client,'yaps-meeting')
        
        # 2. 定義同步的 DB 更新邏輯
        def sync_db_logic():
            crud = TaskCRUD(db)
            for index, item in enumerate(contents):
                key_start = item.key.find('/') + 1
                key = item.key[key_start:] if key_start != -1 else item.key
                
                record = crud.get_task_by_key(key)
                
                if record is None:
                    new_task = {
                        "id": str(uuid4()),
                        "object_key": key,
                        "region": 'cn-hongkong',
                        "size": item.size,
                        "last_modified": dates[index],
                        "status": "NONE"
                    }
                    crud.create_task(new_task)
                    logger.info(f"Synced new file: {key}")
                else:
                    crud.update_task(
                        record, 
                        size=item.size, 
                        last_modified=dates[index]
                    )
            return db.exec(select(Task)).all()

        # 3. [關鍵修改] 在線程池中運行同步 DB 邏輯
        all_records = await run_in_threadpool(sync_db_logic)
        return all_records

    except Exception as e:
        logger.error(f"Error syncing files: {e}")
        return []
    finally:
        await client.close()

@app.post("/api/upload/{region}")
async def upload_file(region: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    return {"status":"function not ready yet"}
    
@app.get("/api/download/{region}/{object_key}")
async def download_file(region:str, object_key: str):
    return

# [修改] 解決 async def 混用同步 DB 查詢的問題
@app.get("/api/meetings/detail")
async def file_detail(object_key: str, db: Session = Depends(get_db)):
    print(f"getting {object_key}")
    
    # 1. [關鍵修改] 使用線程池執行同步查詢
    def get_task_sync():
        crud = TaskCRUD(db)
        return crud.get_task_by_key(object_key)
    
    db_task = await run_in_threadpool(get_task_sync)
    
    if db_task is None:
        raise HTTPException(status_code=404, detail="Subtitle not found")
    
    client = aos.init_client(is_async=False, endpoint='custom')

    try:
        # 獲取 URL 本身如果是同步的，也建議用 to_thread，或者如果 client 是異步的則 await
        url = await asyncio.to_thread(aos.get_object_url, client, object_key)
    except Exception as e:
        raise HTTPException(500, f"Error getting url: {e}")

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
    # [修改] 讀取 Render 環境變量中的 PORT，如果沒有則默認 8000
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)