from fastapi import FastAPI, HTTPException, Depends, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from auth import verify_password, get_password_hash, create_access_token, decode_access_token

app = FastAPI()

# 1. 严格的 CORS 配置
origins = [
    "http://localhost:5173", # 前端地址
    "http://127.0.0.1:5173"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True, # 允许携带 Cookie
    allow_methods=["*"],
    allow_headers=["*"],
)

# 模拟数据库 (存储的是哈希后的密码)
# 原始密码是 "secret123"
fake_users_db = {
    "admin": {
        "username": "admin",
        "hashed_password": get_password_hash("secret123") 
    }
}

class LoginRequest(BaseModel):
    username: str
    password: str

# 依赖项：从 Cookie 获取当前用户
async def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
        
    user = fake_users_db.get(payload.get("sub"))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

@app.post("/login")
async def login(response: Response, data: LoginRequest):
    user = fake_users_db.get(data.username)
    if not user or not verify_password(data.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    # 生成 JWT
    access_token = create_access_token(data={"sub": user["username"]})

    # 2. 将 Token 写入 HttpOnly Cookie
    # httponly=True: JS 无法读取 (防XSS)
    # samesite='lax': 防止 CSRF
    # secure=False: 本地开发用 False (HTTP), 生产环境必须 True (HTTPS)
    response.set_cookie(
        key="access_token", 
        value=access_token, 
        httponly=True, 
        max_age=1800,
        samesite='lax',
        secure=False 
    )
    return {"message": "Login successful"}

@app.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"message": "Logged out"}

# 受保护的路由，用于前端验证 Session
@app.get("/users/me")
async def read_users_me(current_user: dict = Depends(get_current_user)):
    return {"username": current_user["username"]}