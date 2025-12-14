import os
from dotenv import load_dotenv
from typing import Literal, Optional

import http.client

import asyncio
import alibabacloud_oss_v2 as oss
import alibabacloud_oss_v2.aio as oss_aio


def init_client(is_asycn=True, region='cn-hongkong', endpoint=None):  # endpoint=Optional[Literal["internal", "custom"]]
    # 从环境变量中加载凭证信息，用于身份验证
    credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()

    # 加载SDK的默认配置，并设置凭证提供者
    cfg = oss.config.load_default()
    cfg.credentials_provider = credentials_provider

    # 方式一：只填写Region（推荐）
    # 必须指定Region ID，以华东1（杭州）为例，Region填写为cn-hangzhou，SDK会根据Region自动构造HTTPS访问域名
    cfg.region = region

    # different ways to create client: origin / internal / custom domain
    if endpoint is not None:
        match endpoint:
            case "internal":
                cfg.use_internal_endpoint = True
            case "custom":
                # 设置自定义域名，例如“http://static.example.com”
                cfg.endpoint = "ecmeetings.org"
                # 设置使用CNAME
                cfg.use_cname = True
            case _:
                raise ValueError(
                    f"Invalid endpoint value: {endpoint!r}. "
                    "Only 'internal' or 'custom' are allowed."
                )

    # 使用配置好的信息创建OSS同步/异步客户端
    if is_asycn:
        client = oss_aio.AsyncClient(cfg)
    else:
        client = oss.Client(cfg)

    return client


async def upload_file(client, key: str):
    """
        PUT NEW OBJECT
    """
    try:
        # 定义要上传的字符串内容
        text_string = "Hello, OSS!"
        data = text_string.encode('utf-8')  # 将字符串编码为UTF-8字节串

        # 执行异步上传对象的请求，指定存储空间名称、对象名称和数据内容
        # 注意：使用 await 关键字等待异步操作完成
        result = await client.put_object(
            oss.PutObjectRequest(
                bucket="Your Bucket Name",
                key="Your Object Key",
                body=data,
            )
        )

        # objects = client.list_objects_v2()

        # 输出请求的结果状态码、请求ID、ETag，用于检查请求是否成功
        print(f'status code: {result.status_code}\n'
            f'request id: {result.request_id}\n'
            f'etag: {result.etag}'
        )

    except Exception as e:
        print(f'上传失败: {e}')

    finally:
        # 关闭异步客户端连接（重要：避免资源泄漏）
        await client.close()


async def get_all_files(client, bucket_name, prefix=""):
    """
    get all objects from aliyuncs async client
    """
    try:
        # 创建ListObjectsV2操作的分页器
        objects = await client.list_objects_v2(oss.ListObjectsV2Request(
                bucket=bucket_name,
                prefix=prefix
            ))

        print(objects)
        return objects
    except Exception as e:
        print("error while getting all: ",e)


def get_object_url(client, object_key):
    """ 
    do not use asyncClient
    Get object's download url.
    Use internal client when submit to tingwu server.
    Use custom client when need a preview or download link.
    """
    try:
        pre_result = client.presign(
        oss.GetObjectRequest(
            bucket='yaps-meeting',  # 指定存储空间名称 / hard code for now
            key=object_key,        # 指定对象键名
        ))
        return pre_result.url
    
    except Exception as e:
        print("Error while getting the url: ",e)

    
async def main():
    ak = os.getenv("OSS_ACCESS_KEY_ID")
    sk = os.getenv("OSS_ACCESS_KEY_SECRET")
    region = 'cn-hongkong'
    bucket_name = 'yaps-meeting'
    client = init_client(region)
    await get_all_files(client, bucket_name)

# 当此脚本被直接运行时，调用main函数
if __name__ == "__main__":
    # 使用 asyncio.run() 运行异步主函数
    asyncio.run(main())