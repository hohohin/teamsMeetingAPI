# main.py
import os
import asyncio
import logging
import tos
import json

from uuid import uuid4
from urllib.parse import quote
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session


# 导入自定义模块
from database_manuplate.database_alchemy import init_db, SessionLocal, SubsCRUD, SubsMetaDB
from models import SubsMeta
import server  # 你的阿里云交互代码
from aos import init_client, get_all_files

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

# --- 数据库依赖 ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- 核心：后台处理逻辑 ---

async def process_submission(db: Session):
    """
    阶段 1: 查找 status='NONE' 的记录 -> 构造URL -> 提交给阿里云 -> 更新为 'ONGOING'
    """
    crud = SubsCRUD(db)
    pending_tasks = crud.get_tasks_by_status("NONE")
    
    for task in pending_tasks:
        try:
            logger.info(f"[Submit] Processing pending task: {task.object_key}")
            
            # 1. 构造 TOS URL
            bucket, endpoint = get_tos_config(task.region)
            # 注意：如果是私有读Bucket，这里需要生成带签名的URL, 替换 URL 生成逻辑为：
            # client = tos.TosClientV2(TOS_AK, TOS_SK, endpoint, task.region)
            # file_url = client.generate_presigned_url("GET", bucket, task.object_key, expires=3600)

            # 这里假设是公共读或者Tingwu服务器有权限访问
            # file_url = f"https://{bucket}.{endpoint}/{quote(task.object_key)}"

            
            # 2. 调用 Server 代码提交任务 (运行在线程池中以免阻塞)
            # 使用 task.id 作为 task_key，方便后续追踪
            res = await asyncio.to_thread(server.submit_task, cdn_url, task.id)
            
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
    crud = SubsCRUD(db)
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
                
                crud.update_task(task, status="COMPLETED", query_res=result_dict)
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
            with SessionLocal() as db:
                # 1. 处理提交 (NONE -> ONGOING)
                await process_submission(db)
                
                # 2. 处理查询 (ONGOING -> COMPLETED)
                await process_polling(db)
                
            await asyncio.sleep(5) # 休息5秒
        except Exception as e:
            logger.error(f"Critical error in background worker: {e}")
        await asyncio.sleep(30)

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

@app.get("/api/files/{region}")
async def get_files(region: str, db: Session = Depends(get_db)):
    """同步 OSS 文件列表到数据库"""
    if not TOS_AK or not TOS_SK:
        raise HTTPException(status_code=500, detail="TOS credentials missing")

    try:
        bucket_name, endpoint = get_tos_config(region)
        client = tos.TosClientV2(TOS_AK, TOS_SK, endpoint, region)
        
        truncated = True
        continuation_token = ''
        crud = SubsCRUD(db)

        while truncated:
            result = await asyncio.to_thread(
                client.list_objects_type2, bucket_name, continuation_token=continuation_token # type: ignore
            )
            
            for item in result.contents:
                # 检查数据库是否存在
                record = crud.get_sub_by_key(item.key)
                
                if record is None:
                    # 发现新文件，插入数据库，状态设为 NONE (等待后台自动提交)
                    new_sub = {
                        "id": str(uuid4()),
                        "object_key": item.key,
                        "region": region,
                        "size": item.size,
                        "last_modified": item.last_modified.strftime("%Y-%m-%d %H:%M:%S"),
                        "status": "NONE"
                    }
                    crud.create_sub(new_sub)
                    logger.info(f"Synced new file: {item.key}")
                else:
                    # 更新已有文件信息
                    crud.update_task(
                        record, 
                        size=item.size, 
                        last_modified=item.last_modified.strftime("%Y-%m-%d %H:%M:%S")
                    )
            
            truncated = result.is_truncated
            continuation_token = result.next_continuation_token
            
        # 返回所有文件记录
            all_records = crud.db.query(SubsMetaDB).all()
        # 注意：实际生产中这里应该分页，否则数据库大时会卡死
        return all_records

    except Exception as e:
        logger.error(f"Error syncing files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload/{region}")
async def upload_file(region: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传文件到 TOS 并写入数据库 (Status=NONE)"""
    if not TOS_AK or not TOS_SK:
        raise HTTPException(status_code=500, detail="TOS credentials missing")
    if file.filename is None:
        return

    object_key = file.filename
    bucket_name, endpoint = get_tos_config(region)
    crud = SubsCRUD(db)

    try:
        # 1. 检查数据库是否存在
        existing = crud.get_sub_by_key(object_key)
        if existing:
            # 简单策略：如果存在则报错，或者覆盖
            # 这里选择覆盖上传，但要重置状态
            pass 

        # 2. 上传到 TOS (异步运行)
        client = tos.TosClientV2(TOS_AK, TOS_SK, endpoint, region)
        
        # 读取文件内容
        content = await file.read()
        
        res = await asyncio.to_thread(
            client.put_object, bucket_name, object_key, content=content
        )

        if res.status_code == 200:
            # 3. 写入/更新数据库
            sub_data = {
                "id": str(uuid4()),
                "object_key": object_key,
                "region": region,
                "status": "NONE", # 关键：设置为 NONE，让后台 Worker 自动捕获并提交
                "last_modified": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            if existing:
                crud.update_task(existing, status="NONE", region=region) # 重置状态以便重新听悟
                result_id = existing.id
            else:
                new_sub = crud.create_sub(sub_data)
                result_id = new_sub.id
            
            logger.info(f"File {object_key} uploaded and DB record created.")
            return {"message": "Upload success", "id": result_id, "status": "queued"}
        else:
            raise HTTPException(status_code=500, detail=f"TOS Upload failed: {res.status_code}")

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
# 下载会议文件
@app.get("/api/download/{region}/{object_key}")
async def download_file(region:str, object_key: str):
    # 后续可添加属性：下载次数
    print(f"object key get: {object_key}, in {region}")
    # ================= TOS Client 初始化 =================
    match region:
        case "guangzhou":
            bucket_name = "yings-meeting"
            endpoint="tos-cn-guangzhou.volces.com"
        case "hongkong":
            bucket_name = "bucket4hk"    
            endpoint="tos-cn-hongkong.volces.com"
    # 简单的配置检查，防止因空配置导致的连接错误
    if not TOS_AK or not TOS_SK:
        logger.warning("⚠️ 警告: TOS 配置信息(AK/SK)似乎为空。请检查 main.py 中的配置区域或环境变量。")
        return
    try:
    # =========== 创建 TosClientV2 对象，对桶和对象的操作都通过 TosClientV2 实现
        client = tos.TosClientV2(TOS_AK, TOS_SK, endpoint, region)

        # def percentage(consumed_bytes, total_bytes, rw_once_bytes, type: DataTransferType):
        #     if total_bytes:
        #         rate = int(100 * float(consumed_bytes) / float(total_bytes))
        #         print("rate:{}, consumed_bytes:{},total_bytes{}, rw_once_bytes:{}, type:{}"
        #               .format(rate, consumed_bytes,total_bytes,rw_once_bytes, type))

        # # 可通过 data_transfer_listener=percentage配置下载对象进度条
        # object_stream = client.get_object_to_file(bucket_name, object_key, '\Download', data_transfer_listener=percentage)
        # # 迭代读取对象内容 client.get_object_to_file(bucket_name, object_key, file_name)
        # for content in object_stream:
        #     print(f"downloading content: {content}")
        print(f"downloading {object_key}")
        # 1. 获取 TOS 对象流
        output = client.get_object(bucket_name, object_key)
        
        # 2. 定义一个生成器函数来迭代读取数据
        # 这里的 output 对应你代码中的 object_stream
        def iterfile():
            # 这里的 chunk_size 可以根据网络情况调整，通常 4KB 到 1MB 之间
            # 如果 output 本身就是可迭代的 (如你的注释所示)，直接 yield 即可
            for chunk in output:
                yield chunk
            
            # 如果 SDK 需要显式关闭流，可以在这里处理
            # output.close() 

        # 2.1 对文件名进行 URL 编码
        # 比如 "副本.png" 会变成 "%E5%89%AF%E6%9C%AC.png"
        encoded_filename = quote(object_key)

        # 3. 设置响应头
        # filename*=utf-8''... 是现代浏览器处理非 ASCII 文件名的标准写法
        headers = {
            "Content-Disposition": f"attachment; filename*=utf-8''{encoded_filename}",
            "Content-Type": "application/octet-stream"
        }

        # 4. 返回流式响应
        return StreamingResponse(iterfile(), headers=headers)

    except tos.exceptions.TosClientError as e:
        # 操作失败，捕获客户端异常，一般情况为非法请求参数或网络异常
        print('fail with client error, message:{}, cause: {}'.format(e.message, e.cause))
    except tos.exceptions.TosServerError as e:
        # 操作失败，捕获服务端异常，可从返回信息中获取详细错误信息
        print('fail with server error, code: {}'.format(e.code))
        # request id 可定位具体问题，强烈建议日志中保存
        print('error with request id: {}'.format(e.request_id))
        print('error with message: {}'.format(e.message))
        print('error with http code: {}'.format(e.status_code))
        print('error with ec: {}'.format(e.ec))
        print('error with request url: {}'.format(e.request_url))
    except Exception as e:
        print('fail with unknown error: {}'.format(e))

# 刪除記錄


# 详情页
@app.get("/api/meetings/{object_key}")
async def file_detail(object_key: str, db: Session = Depends(get_db)):
    crud = SubsCRUD(db)
    db_sub = crud.get_sub_by_key(object_key)
    
    if db_sub is None:
        raise HTTPException(status_code=404, detail="Subtitle not found")
    
    # 如果 query_res 是 JSON 字符串，可以反序列化为 dict（可选）
    result = {
        "id": db_sub.id,
        "object_key": db_sub.object_key,
        "region": db_sub.region,
        "size": db_sub.size,
        "task_id": db_sub.task_id,
        "status": db_sub.status,
        "query_res": json.loads(db_sub.query_res) if db_sub.query_res else {},
        "created_at": db_sub.created_at,
        "last_modified": db_sub.last_modified,
    }
    
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)