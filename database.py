# database.py
import os
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlalchemy import create_engine, Column, String, Integer, BigInteger, Text, DateTime, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# --- 修改开始 ---

# 1. 尝试从环境变量获取 DATABASE_URL（Render 会自动注入这个变量）
# 如果没有（比如在本地运行），则默认使用 sqlite
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./subtitles.db")

connect_args = {}

# 2. 针对不同数据库的特殊配置
if DATABASE_URL.startswith("sqlite"):
    # SQLite 特有配置
    connect_args = {"check_same_thread": False}
else:
    # PostgreSQL 配置
    # 【关键修复】Render 提供的 URL 通常是 postgres://，但 SQLAlchemy 需要 postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# 3. 创建引擎
engine = create_engine(DATABASE_URL, connect_args=connect_args)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

class SubsMetaDB(Base):
    __tablename__ = "transcripted"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    object_key = Column(String, index=True)
    region = Column(String, default="hongkong") # 新增：用于构建URL
    size = Column(BigInteger, default=0)
    task_id = Column(String, default="")
    status = Column(String, default="NONE") 
    query_res = Column(Text, default="{}")
    chapters = Column(Text, default="{}")
    summary = Column(Text, default="{}")
    transcripts = Column(Text, default="{}")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_modified: Mapped[str] = mapped_column(String, default="")

def init_db():
    Base.metadata.create_all(bind=engine)

class SubsCRUD:
    def __init__(self, db):
        self.db = db

    def create_sub(self, sub_data: dict) -> SubsMetaDB:
        if isinstance(sub_data.get("query_res"), dict):
            sub_data["query_res"] = json.dumps(sub_data["query_res"], ensure_ascii=False)
        db_sub = SubsMetaDB(**sub_data)
        self.db.add(db_sub)
        self.db.commit()
        self.db.refresh(db_sub)
        return db_sub

    def get_sub(self, sub_id: str):
        return self.db.scalar(select(SubsMetaDB).where(SubsMetaDB.id == sub_id))

    def get_sub_by_key(self, object_key: str):
        return self.db.scalar(select(SubsMetaDB).where(SubsMetaDB.object_key == object_key))

    def get_tasks_by_status(self, status: str):
        """通用状态获取函数"""
        return self.db.scalars(select(SubsMetaDB).where(SubsMetaDB.status == status)).all()

    def update_task(self, db_obj: SubsMetaDB, **kwargs):
        """通用更新函数"""
        for key, value in kwargs.items():
            if key in ["query_res","chapters","summary","transcripts"] and isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            setattr(db_obj, key, value)
        
        db_obj.last_modified = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.db.commit()
        self.db.refresh(db_obj)
        return db_obj

# 依赖注入 Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()