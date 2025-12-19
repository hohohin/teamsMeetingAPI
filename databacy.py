import os
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

# 引入 SQLModel 及相关组件
from sqlmodel import SQLModel, Field, Session, select, create_engine
# 引入 SQLAlchemy 类型以保持数据库 Schema 的精确控制 (Text, BigInteger)
from sqlalchemy import Column, Text, BigInteger, DateTime

# 1. 数据库 URL 配置 (保持不变)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///yaps.db")

connect_args = {}

# 2. 针对不同数据库的特殊配置 (保持不变)
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
else:
    # Render PostgreSQL 修复
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# 3. 创建引擎 (使用 SQLModel 的 create_engine，本质是 SQLAlchemy 的封装)
engine = create_engine(DATABASE_URL, connect_args=connect_args)

# 4. 定义模型
class Task(SQLModel, table=True):
    id: str = Field(primary_key=True)
    object_key: str = Field(index=True)
    region: str = Field(default="hongkong")
    
    # 使用 sa_column 强制使用 BigInteger，对应原代码的 BigInteger
    size: int = Field(default=0, sa_column=Column(BigInteger))
    
    task_id: str = Field(default="")
    status: str = Field(default="NONE")
    
    # 使用 sa_column 强制使用 Text 类型 (用于存储长 JSON 字符串)，防止被截断
    query_res: str = Field(default="{}", sa_column=Column(Text))
    chapters: str = Field(default="{}", sa_column=Column(Text))
    summary: str = Field(default="{}", sa_column=Column(Text))
    transcripts: str = Field(default="{}", sa_column=Column(Text))
    
    # default_factory 用于动态生成时间
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True)) 
    )
    
    last_modified: str = Field(default="")

class User(SQLModel, table=True):
    # id 是主键，Optional 是因为创建新用户时 id 还没生成（由数据库生成）
    id: Optional[int] = Field(default=None, primary_key=True)
    # index=True 会给属性建索引，这样查找用户会非常快
    agent_code: str = Field(index=True, unique=True)
    username: Optional[str] = Field(default='未设置用户名')
    # ⚠️ 注意：我们存的是加密后的字符串，所以字段名最好叫 hashed_password
    hashed_password: str

class UserCreate(SQLModel):
    agent_code: str
    username: Optional[str] = '未设置用户名'
    password: str

class UserRead(SQLModel):
    agent_code: str
    username: Optional[str] = '未设置用户名'

# 初始化数据库
def init_db():
    SQLModel.metadata.create_all(engine)

# CRUD 类
class TaskCRUD:
    def __init__(self, db: Session):
        self.db = db

    def create_task(self, task_data: dict) -> Task:
        # 处理 JSON 序列化逻辑 (保持原有逻辑)
        if isinstance(task_data.get("query_res"), dict):
            task_data["query_res"] = json.dumps(task_data["query_res"], ensure_ascii=False)
        
        # SQLModel 直接通过关键字参数解包创建实例
        db_task = Task(**task_data)
        self.db.add(db_task)
        self.db.commit()
        self.db.refresh(db_task)
        return db_task

    def get_task(self, task_id: str) -> Optional[Task]:
        # 使用 exec().first() 替代 scalar()
        statement = select(Task).where(Task.id == task_id)
        return self.db.exec(statement).first()

    def get_task_by_key(self, object_key: str) -> Optional[Task]:
        statement = select(Task).where(Task.object_key == object_key)
        return self.db.exec(statement).first()

    def get_tasks_by_status(self, status: str):
        """通用状态获取函数"""
        statement = select(Task).where(Task.status == status)
        return self.db.exec(statement).all()

    def update_task(self, db_obj: Task, **kwargs) -> Task:
        """通用更新函数"""
        for key, value in kwargs.items():
            # 保持原有的 JSON 序列化逻辑
            if key in ["query_res", "chapters", "summary", "transcripts"] and isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            setattr(db_obj, key, value)
        
        db_obj.last_modified = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.db.add(db_obj) # 显式 add 是好习惯，虽然修改对象通常会自动 track
        self.db.commit()
        self.db.refresh(db_obj)
        return db_obj
    
class UserCRUD:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_user(self, user: User):
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user
    
    def delete_user(self):
        return
    
    def read_user_by_code(self, user: User):
        statement = select(User).where(User.agent_code == user.agent_code)
        return self.db.exec(statement).first()

    def update_user(self, user:User):
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user        


# 依赖注入 Dependency
def get_db():
    # SQLModel 推荐使用上下文管理器语法
    with Session(engine) as session:
        yield session