import os

from alibabacloud_tingwu20230930.client import Client as tingwu20230930Client
from alibabacloud_credentials.client import Client as CredentialClient
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tingwu20230930 import models as tingwu_20230930_models
from alibabacloud_tea_util import models as util_models
from alibabacloud_tea_util.client import Client as UtilClient

from dotenv import load_dotenv

import aos

class QueryResult:
    MeetingAssistance: str
    AutoChapters: str
    Transcription: str
    Summarization: str

# def create_client() -> tingwu20230930Client:
#     """
#     使用凭据初始化账号Client
#     @return: Client
#     @throws Exception
#     """
#     # 工程代码建议使用更安全的无AK方式，凭据配置方式请参见：https://help.aliyun.com/document_detail/378659.html。
#     credential = CredentialClient()
#     config = open_api_models.Config(
#         credential=credential
#     )
#     # Endpoint 请参考 https://api.aliyun.com/product/tingwu
#     config.endpoint = f'tingwu.cn-beijing.aliyuncs.com'
#     return tingwu20230930Client(config)

load_dotenv()

def create_client() -> tingwu20230930Client:
    """
    显式使用 AK/SK 初始化 Tingwu 客户端，避免在子线程中触发信号注册。
    """
    ak = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")
    sk = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")

    if not ak or not sk:
        raise ValueError(
            "Missing ALIBABA_CLOUD_ACCESS_KEY_ID or ALIBABA_CLOUD_ACCESS_KEY_SECRET. "
            "Please set them in environment variables."
        )

    config = open_api_models.Config(
        access_key_id=ak,
        access_key_secret=sk,
        endpoint="tingwu.cn-beijing.aliyuncs.com"
    )
    return tingwu20230930Client(config)


def submit_task(oss_internal_client, task_key: str):
    # 生成预签名的GET请求
    internal_url = aos.get_object_url(oss_internal_client, task_key)
    print(f"internal_url: {internal_url}")

    # stt client
    client = create_client()
    parameters_summarization = tingwu_20230930_models.CreateTaskRequestParametersSummarization(
        types=[
            'Paragraph'
        ]
    )
    parameters_meeting_assistance = tingwu_20230930_models.CreateTaskRequestParametersMeetingAssistance(
        types=[
            'KeyInformation'
        ]
    )
    parameters = tingwu_20230930_models.CreateTaskRequestParameters(
        auto_chapters_enabled=True,
        meeting_assistance_enabled=True,
        meeting_assistance=parameters_meeting_assistance,
        summarization_enabled=True,
        summarization=parameters_summarization
    )
    input = tingwu_20230930_models.CreateTaskRequestInput(
        source_language='fspk',
        file_url= internal_url,
        task_key= task_key
    )
    create_task_request = tingwu_20230930_models.CreateTaskRequest(
        type='offline',
        app_key='yQbJjFiy2CkdozLz',
        input=input,
        parameters=parameters
    )
    runtime = util_models.RuntimeOptions()
    headers = {}
    try:
        res = client.create_task_with_options(create_task_request, headers, runtime)
        # res sample {
        #   "Code": "0",
        #   "Data": {
        #     "TaskId": "3d904bb9a279403c8bcc535eb56a575e",
        #     "TaskKey": "task_tingwu_123",
        #     "TaskStatus": "ONGOING"
        #   },
        #   "Message": "success",
        #   "RequestId": "2C771B34-8B01-5C7E-92A0-64150BDDDD0B"
        # }
        if res.body.message != "success":
            return {"task_id": "", "status": res.body.data.task_status}

        return {"task_id": res.body.data.task_id, "status": res.body.data.task_status}
    except Exception as error:
        # 此处仅做打印展示，请谨慎对待异常处理，在工程项目中切勿直接忽略异常。
        # 错误 message
        print(f"error: {error}")
        # 诊断地址
        # print(error.data.get("Recommend"))
        # UtilClient.assert_as_string(error.message)

def query_task(task_id: str):
    client = create_client()
    runtime = util_models.RuntimeOptions()
    headers = {}
    try:
        # 复制代码运行请自行打印 API 的返回值
        res = client.get_task_info_with_options(task_id, headers, runtime)
        return res
    except Exception as error:
        # 此处仅做打印展示，请谨慎对待异常处理，在工程项目中切勿直接忽略异常。
        # 错误 message
        print(f"error: {error}")
        # 诊断地址
        # print(error.data.get("Recommend"))
        # UtilClient.assert_as_string(error)

import time

def query_loop(task_id: str, max_retries: int = 30, retry_interval: float = 2.0):
    """
    轮询查询任务状态，直到任务完成或超时。

    :param task_id: 任务ID
    :param max_retries: 最大重试次数，默认30次（即最多等待60秒）
    :param retry_interval: 每次重试的间隔时间（秒），默认2秒
    :return: 任务结果（当状态为 COMPLETED 时）
    :raises TimeoutError: 如果超过最大重试次数仍未完成
    :raises RuntimeError: 如果任务状态异常（如 FAILED）
    """
    for count in range(max_retries + 1):
        try:
            res = query_task(task_id)
            if not res or not hasattr(res, 'body') or not hasattr(res.body, 'data'):
                raise RuntimeError("Invalid response from query_task")

            status = res.body.data.task_status

            match status:
                case "COMPLETED":
                    return res.body.data.result
                case "FAILED" | "ERROR":
                    raise RuntimeError(f"Task failed with status: {status}")
                case "ONGOING":
                    if count < max_retries:
                        time.sleep(retry_interval)
                    else:
                        raise TimeoutError(f"Task {task_id} did not complete within the allowed time.")
                case _:
                    raise RuntimeError(f"Unknown task status: {status}")

        except Exception as e:
            # 可选择是否在每次异常时重试，这里简单重试
            if count >= max_retries:
                raise e
            time.sleep(retry_interval)

    raise TimeoutError("Unexpected exit from polling loop")