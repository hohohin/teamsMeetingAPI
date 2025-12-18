from sqlmodel import Field, SQLModel, Session
from typing import Optional

class User(SQLModel, table=True):
    # id 是主键，Optional 是因为创建新用户时 id 还没生成（由数据库生成）
    id: Optional[int] = Field(default=None, primary_key=True)
    # index=True 会给属性建索引，这样查找用户会非常快
    agent_code: str = Field(index=True, unique=True)
    username: Optional[str] = '未设置用户名'
    # ⚠️ 注意：我们存的是加密后的字符串，所以字段名最好叫 hashed_password
    hashed_password: str

class UserCreate(SQLModel):
    agent_code: str
    username: Optional[str] = '未设置用户名'
    password: str

class UserRead(SQLModel):
    agent_code: str
    username: Optional[str] = '未设置用户名'


from sqlmodel import SQLModel, create_engine

# 1. 定义数据库文件名
sqlite_file_name = "user_database.db"
# 2. 拼接连接字符串 (相对路径)
sqlite_url = f"sqlite:///{sqlite_file_name}"

# 3. SQLite 特有的配置：允许在多线程环境（如 FastAPI）中使用同一个连接
connect_args = {"check_same_thread": False}

# 4. 创建引擎
engine = create_engine(sqlite_url, connect_args=connect_args)

# 5. 定义一个初始化数据库的函数
def create_db_and_tables():
    # 这行代码会根据所有继承了 SQLModel 的类，去数据库里创建对应的表
    SQLModel.metadata.create_all(engine)

# 2. 定义获取数据库会话的依赖函数
def get_session():
    with Session(engine) as session:
        yield session