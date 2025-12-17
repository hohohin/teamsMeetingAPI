from datetime import datetime, timedelta
from jose import jwt

from fastapi import HTTPException, Request, status
from jose import JWTError, jwt


# ⚠️ 生产环境中，这个密钥必须由随机字符组成，且放在环境变量中！
# 你可以用 `openssl rand -hex 32` 生成一个
SECRET_KEY = "your_super_secret_key_here_please_change_it"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


# 1. 专门用于从 Cookie 中提取 Token 的依赖项
async def get_current_user(request: Request):
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
        
    return username

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    
    # 1. 计算过期时间
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # 2. 将过期时间加入我们要加密的数据中 ('exp' 是标准字段名)
    to_encode.update({"exp": expire})
    
    # 3. 使用密钥和算法生成最终的 JWT 字符串
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt