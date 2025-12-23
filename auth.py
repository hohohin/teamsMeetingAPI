import os
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Request, status, Depends
from sqlmodel import Session, select
from jose import JWTError, jwt

from databacy import get_db, User


# ⚠️ 生产环境中，这个密钥必须由随机字符组成，且放在环境变量中！
# 你可以用 `openssl rand -hex 32` 生成一个
SECRET_KEY = os.getenv("SECRET_KEY", "your_super_secret_key_here_please_change_it")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


# 1. 专门用于从 Cookie 中提取 Token 的依赖项
async def get_current_user(request: Request, session: Session = Depends(get_db)):
    # 从 Cookie 中取出 token 字符串
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    
    # 因为我们在存的时候加了 "Bearer " 前缀，这里要去掉它
    # 格式: "Bearer <token>"
    scheme, _, param = token.partition(" ") 
    if not scheme or scheme.lower() != "bearer":
         raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token format")
    
    try:
        # 解码 Token
        payload = jwt.decode(param, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired or invalid")
        
    # 去数据库捞人🎣
    user_session = select(User).where(User.agent_code == username)
    user = session.exec(user_session).first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",  # 或者 "Could not validate credentials"
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    
    # 1. 计算过期时间
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # 2. 将过期时间加入我们要加密的数据中 ('exp' 是标准字段名)
    to_encode.update({"exp": expire})
    
    # 3. 使用密钥和算法生成最终的 JWT 字符串
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt