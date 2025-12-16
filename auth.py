from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

# 指定的固定用户信息
VALID_USERS = {
    "UserOne": "userName123",
    "Admin": "admin888"
}

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/login")
async def login(data: LoginRequest):
    stored_password = VALID_USERS.get(data.username)
    if stored_password and stored_password == data.password:
        # 实际开发中这里应返回 JWT
        return {"token": "fake-jwt-token-for-" + data.username, "status": "success"}
    
    raise HTTPException(status_code=401, detail="用户名或密码错误")