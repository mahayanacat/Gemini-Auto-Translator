import os
import threading
import time
import random
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from docx import Document
import requests
import logging
from fastapi.staticfiles import StaticFiles
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
import re
import gc
import uvicorn
import webbrowser
import json
import PyPDF2
import docx2txt
import codecs

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = FastAPI()

# Google Gemini API 配置
API_KEY = "YOUR_API_KEY"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent?key={API_KEY}"

# *** 速率限制配置 ***
REQUESTS_PER_MINUTE = 10  # 限制每分钟10个请求 (可根据实际情况调整)
TIME_WINDOW = 60  # 秒

# *** 令牌桶算法配置 *** (简化)
BUCKET_CAPACITY = 10      # 令牌桶容量 (可根据实际情况调整)
REFILL_RATE = 1           # 每秒补充1个令牌 (可根据实际情况调整)
TOKENS = BUCKET_CAPACITY
LAST_REFILL = time.time()

# *** 并发线程池 ***
MAX_WORKERS = 1 # 根据需要调整，但速率限制是主要瓶颈
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# 代理设置
PROXY = {"http": "http://000.0.0.0:0000", "https": "http://000.0.0.0:0000"} # 恢复代理设置

# *** 全局变量 ***
is_translating = True
is_thread_running = False
global_file_path = ""
global_style = ""
global_temperature = ""
global_temp_file_path = ""

# *** 临时文件和标志文件路径 ***
OUTPUT_DIR = "D:/gemini/gemini-translated/"  # 输出目录
TEMP_DIR = os.path.join(OUTPUT_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True) # 确保临时目录存在

IS_UPLOADING_FLAG = os.path.join(TEMP_DIR, "is_uploading.flag") # 上传标志文件
IS_CHECKING_FLAG = os.path.join(TEMP_DIR, "is_checking.flag") # 校对标志文件

consecutive_api_failures = 0  #  记录连续 API 请求失败的次数
MAX_CONSECUTIVE_API_FAILURES = 5  #  最大连续 API 请求失败次数阈值

# 令牌桶算法装饰器 (简化)
def token_bucket(func):
    def wrapper(*args, **kwargs):
        global TOKENS, LAST_REFILL
        now = time.time()
        time_since_last_refill = now - LAST_REFILL
        TOKENS = min(BUCKET_CAPACITY, TOKENS + time_since_last_refill * REFILL_RATE)
        LAST_REFILL = now
        if TOKENS >= 1:
            TOKENS -= 1
            return func(*args, **kwargs)
        else:
            wait_time = (1 - TOKENS) / REFILL_RATE
            logging.warning(f"令牌桶为空，等待 {wait_time:.2f} 秒...")
            time.sleep(wait_time)
            # 添加一个小的随机延迟，以避免所有线程同时醒来并争夺令牌
            time.sleep(random.uniform(0.05, 0.1))
            return wrapper(*args, **kwargs)
    return wrapper


# 封装 API 请求函数，并使用令牌桶装饰和指数退避
@token_bucket
def call_gemini_api(data, headers, max_retries=5):
    """封装 API 请求函数，并使用令牌桶装饰和指数退避."""
    global consecutive_api_failures, is_translating  # 声明使用全局变量

    for attempt in range(max_retries):
        response = None  # 在 try 块之前初始化 response
        try:
            response = requests.post(API_URL, json=data, headers=headers, proxies=PROXY, timeout=120)  # 显式传递 timeout
            response.raise_for_status()  # 抛出 HTTPError，便于捕获

            consecutive_api_failures = 0  # API 请求成功，重置连续失败计数器
            return response

        except requests.exceptions.RequestException as e:
            status_code = getattr(response, 'status_code', None) if response else None  # 添加 response 检查
            logging.error(f"API 请求失败 (Attempt {attempt + 1}/{max_retries}): {e}, Status Code: {status_code}, URL: {response.url if hasattr(response, 'url') else 'N/A'}")  # 打印状态码和URL

            consecutive_api_failures += 1  # API 请求失败，增加连续失败计数器
            logging.warning(f"连续 API 请求失败次数: {consecutive_api_failures}/{MAX_CONSECUTIVE_API_FAILURES}")

            if consecutive_api_failures >= MAX_CONSECUTIVE_API_FAILURES: #  检查是否达到最大连续失败次数
                logging.error(f"达到最大连续 API 请求失败次数 ({MAX_CONSECUTIVE_API_FAILURES})，自动暂停翻译任务！")
                is_translating = False  #  设置 is_translating 为 False，触发暂停
                return None #  返回 None，让翻译循环知道 API 请求失败了


            if status_code == 429:  # 检查是否是速率限制错误
                wait_time = (3 ** attempt) + random.random()  # 指数退避 + 随机抖动
                logging.warning(f"达到速率限制，等待 {wait_time:.2f} 秒后重试...")
                time.sleep(wait_time)
            elif isinstance(e, requests.exceptions.ConnectionError):
                logging.error(f"连接错误 (Attempt {attempt + 1}/{max_retries}): {e}")
                wait_time = (3 ** attempt) + random.random()
                logging.warning(f"连接错误，等待 {wait_time:.2f} 秒后重试...")
                time.sleep(wait_time)
            else:
                # 其他错误，直接返回 None 或抛出异常
                logging.error(f"API 请求失败: {e}")
                return None  # 或者 raise e
    logging.error("达到最大重试次数，API 请求失败")
    return None

# 读取文档内容
def read_document(file_path):
    try:
        file_extension = os.path.splitext(file_path)[1].lower()

        if file_extension == ".docx":
            doc = Document(file_path)
            text = ""
            for para in doc.paragraphs:
                text += para.text + "\n"
            return text
        elif file_extension == ".doc":
            text = docx2txt.process(file_path)
            return text
        elif file_extension == ".pdf":
            text = ""
            with open(file_path, "rb") as file:
                reader = PyPDF2.PdfReader(file)
                for page_num in range(len(reader.pages)):
                    page = reader.pages[page_num]
                    text += page.extract_text()
            return text
        elif file_extension == ".txt":
            with open(file_path, "r", encoding="utf-8") as file:  # 显式指定编码为 utf-8
                text = file.read()
            return text
        else:
            logging.error(f"不支持的文件类型: {file_extension}")
            return None
    except FileNotFoundError:
        logging.error(f"文件未找到: {file_path}")
        return None
    except Exception as e:
        logging.error(f"读取文件失败: {e}")
        return None

def clean_xml_string(text):
    """移除 XML 不兼容的字符."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)  # 移除控制字符
    return text

def check_translation(original_text, chunk, style, filename, max_retries=3):
    """使用 Gemini API 检查翻译质量，并进行修复"""
    logging.info("正在校对...")

    # 创建该翻译任务对应的临时文件路径
    TEMP_CHECKED_FILE = os.path.join(TEMP_DIR, f"{filename}_temp_checked.txt")  # 已校对临时文件

    prompt = f"""
    你是一个专业的翻译质量校对模型，你的任务是检查给定的中文翻译是否准确、流畅、符合原文的含义和风格。
    请严格按照以下步骤进行：
    1. 准确性评估： 比较译文和原文，判断译文是否准确传达了原文的信息，没有遗漏、添加或曲解原意。
    2. 语言流畅性评估： 评估译文的语言是否自然流畅，符合中文表达习惯，没有生硬或不通顺的语句。
    3. 风格一致性评估： 确认译文的风格是否与原文一致。原文风格：{style}
    4. 详细反馈： 如果译文质量不达标，请给出详细的修改建议，说明具体哪些地方需要改进，并提供修改后的译文。
    5. 最终判断： 给出最终的校对判断，如果翻译没有问题，请直接给出原始译文文档， 如果翻译质量有问题，请给出最终修改后的译文。
    请只给出最终译文，不要包含任何其他内容。原文没有的内容禁止编造。

    这是原文：{original_text}
    这是译文：{chunk}
    """
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }

    corrected_text = ""  # 初始化 corrected_text
    try:
        response = call_gemini_api(data, headers)
        if response and "candidates" in response.json() and response.json()["candidates"]:
            api_response = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip() # 获取校对后的译文

            if "翻译质量优秀，无需修改" in api_response:
                corrected_text = chunk  # 如果API返回无需修改，则使用原始翻译文本
                logging.info("翻译质量优秀，无需修改，使用原始翻译文本")
            else:
                corrected_text = api_response  # 否则使用API返回的校对文本
                logging.info("完成校对，使用API返回的校对文本")

        else:
            logging.warning("校对请求失败，请检查 Gemini API  密钥")
            corrected_text = chunk #校对失败，使用原始翻译文本

    except Exception as e:
        logging.error(f"校对请求失败: {e}")
        corrected_text = chunk  # 校对失败，使用原始翻译文本

    return corrected_text # 返回校对后的文本

def split_into_chunks(text, chunk_size=10000):
    """将文本分割成指定大小的块"""
    chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
    return chunks

def translate_document(file_path: str, style: str, temperature: float):
    global is_thread_running, is_translating

    try:
        text = read_document(file_path)
        if text is None:
            logging.error("读取到的文本是 None")
            is_thread_running = False
            return

        # 1. 定义输出文件路径
        OUTPUT_DIR = "D:/gemini/gemini-translated/"  # 输出目录
        filename = os.path.splitext(os.path.basename(file_path))[0]
        translated_file_path = os.path.join(OUTPUT_DIR, f"{filename}_translated.txt")  # 修改文件扩展名为 .txt

        # 创建该翻译任务对应的临时文件路径
        TEMP_INPUT_FILE = os.path.join(TEMP_DIR, f"{filename}_temp_input.txt") # 原始文档临时文件
        TEMP_TRANSLATED_FILE = os.path.join(TEMP_DIR, f"{filename}_temp_translated.txt") # 原始译文临时文件
        TEMP_CHECKED_FILE =  os.path.join(TEMP_DIR, f"{filename}_temp_checked.txt") #已校对临时文件
        TEMP_CHECKPOINT_FILE = os.path.join(TEMP_DIR, f"{filename}_checkpoint.json") #检查点文件

        # 2. 分割成句子
                # 2. 分割成句子
        def split_into_sentences(text):
            """将文本分割成句子"""
            sentences = text.splitlines()  # 使用换行符分割句子
            return sentences

        sentences = split_into_sentences(text)
        num_sentences = len(sentences)

        # 3. 检查是否存在已翻译的临时文件
        translated_text = ""
        checked_text = ""
        start_sentence_index = 0
        start_chunk_index = 0 #校对起始块

        if os.path.exists(TEMP_TRANSLATED_FILE):
            try:
                with open(TEMP_TRANSLATED_FILE, "r", encoding="utf-8") as f:
                    translated_text = f.read()
                logging.info(f"检测到已存在的翻译临时文件，从上次翻译位置继续。")
            except Exception as e:
                logging.warning(f"读取翻译临时文件失败: {e}, 将从头开始翻译")
                translated_text = ""  # 确保 translated_text 为空

        if os.path.exists(TEMP_CHECKED_FILE):
            try:
                with open(TEMP_CHECKED_FILE, "r", encoding="utf-8") as f:
                    checked_text = f.read()
                logging.info(f"检测到已存在的校对临时文件，从上次校对位置继续。")
            except Exception as e:
                logging.warning(f"读取校对临时文件失败: {e}, 将重新翻译和校对")
                checked_text = "" # 确保 checked_text 为空

        # 4. 检查是否存在检查点文件
        if os.path.exists(TEMP_CHECKPOINT_FILE):
            try:
                with open(TEMP_CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                    checkpoint_data = json.load(f)
                    start_sentence_index = checkpoint_data.get("sentence_index", 0) #上次翻译到的句子
                    start_chunk_index = checkpoint_data.get("chunk_index", 0) #上次校对到的块
                    translated_text = checkpoint_data.get("translated_text", translated_text) #上次已经翻译的文本
                    checked_text = checkpoint_data.get("checked_text", checked_text) #上次已经校对的文本

                    logging.info(f"检测到检查点文件，从上次翻译句子 {start_sentence_index} 和校对块 {start_chunk_index} 继续。")
            except Exception as e:
                logging.warning(f"读取检查点文件失败: {e}, 从头开始翻译和校对")
                start_sentence_index = 0
                start_chunk_index = 0
        else:
             start_sentence_index = 0
             start_chunk_index = 0

        # 5. 如果没有临时文件，则从头开始翻译，需要先将原文写入临时文件
        if not os.path.exists(TEMP_TRANSLATED_FILE) and not os.path.exists(TEMP_CHECKED_FILE):
            with open(TEMP_INPUT_FILE, "w", encoding="utf-8") as f:
                f.write(text)

        # 6. 创建上传标志文件
        open(IS_UPLOADING_FLAG, 'a').close() # 创建一个空文件表示正在上传
        logging.info(f"创建上传标志文件")

        #7. 批量翻译
        batch_size = 300 #根据API情况调整批量大小
        translated_sentences = []

        for i in tqdm(range(start_sentence_index, len(sentences), batch_size), desc = "批量翻译", initial = start_sentence_index):
            if not is_translating:  # 检查是否暂停
                logging.info("翻译任务已暂停")
                break

            batch = sentences[i:i + batch_size]
            batch_text = "\n".join(batch) #合并句子
            logging.info(f"当前处理句子批次：{i // batch_size + 1}/{len(sentences) // batch_size + 1}, 句子索引范围：{i}-{i + len(batch)}")

            # 8. 构建 API 请求数据
            prompt = f"""请将以下文本翻译成流畅的中文，并严格禁止拆分任何段落。完整翻译，禁止节译。只翻译，不解释。禁止编造原文没有的内容（即使你认为原文的结尾还没完整）。正确使用标点符号。请尽可能保留原文的格式和结构。只返回翻译结果，不要包含任何原文、解释、说明、前缀或后缀，确保输出只有翻译后的中文文本。翻译风格：{style}。
            待翻译的文本：
            {batch_text}"""

            headers = {"Content-Type": "application/json"}
            data = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }],
                "generationConfig": {
                    "temperature": temperature
                }
            }

            # 9. 调用 API
            response = call_gemini_api(data, headers)

            if response and "candidates" in response.json() and response.json()["candidates"]:
                translated_batch_text = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                translated_batch = translated_batch_text.split("\n") #分割批量翻译后的文本
                translated_sentences.extend(translated_batch)

            else:
                logging.warning(f"翻译句子批次 {i // batch_size} 失败，使用原文")
                translated_sentences.extend(batch)  # 如果API失败，使用原文

            time.sleep(random.uniform(5, 10)) #避免请求过快
             #  保存临时文件
            try:
                translated_text += translated_batch_text + "\n"  # 添加翻译后的批次文本，并添加换行符
                with open(TEMP_TRANSLATED_FILE, "w", encoding="utf-8") as f:
                    f.write(translated_text) #保存全部已翻译文本
                logging.info(f"已保存翻译句子到临时文件 {TEMP_TRANSLATED_FILE}, 已翻译 {len(translated_sentences)} 句")

                #更新检查点文件
                checkpoint_data = {
                    "sentence_index": i + batch_size,
                    "chunk_index": start_chunk_index,
                    "translated_text": translated_text,
                    "checked_text": checked_text
                }
                with open(TEMP_CHECKPOINT_FILE, "w", encoding="utf-8") as f:
                    json.dump(checkpoint_data, f) #保存所有信息
                logging.info("已更新检查点文件")

                start_sentence_index = i + batch_size #更新翻译起始位置

            except Exception as e:
                logging.error(f"保存翻译句子到临时文件失败: {e}")
                break

        # 10. 翻译完成后，进行校对
        try:
            #创建校对标志
            open(IS_CHECKING_FLAG, 'a').close()  # 创建校对标志
            logging.info("创建校对标志文件")

                        # 分割成块进行校对
            def split_into_chunks(text, chunk_size=10000):
                """将文本分割成指定大小的块"""
                chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
                return chunks

            translated_chunks = split_into_chunks(translated_text)
            corrected_text = checked_text  # 从之前校对的进度开始
            start_chunk_index = len(checked_text) // 10000  # 计算从第几个块开始

            # 从上次校对的位置开始迭代
            for i, chunk in enumerate(tqdm(translated_chunks, desc="批量校对", initial=start_chunk_index, total=len(translated_chunks))):
                if i < start_chunk_index:  # 跳过已经校对的块
                    continue

                if not is_translating:  # 检查是否暂停
                    logging.info("校对任务已暂停")
                    # 保存检查点
                    checkpoint_data = {
                        "sentence_index": start_sentence_index,
                        "chunk_index": i,  # 保存当前块的索引
                        "translated_text": translated_text,
                        "checked_text": corrected_text
                    }
                    with open(TEMP_CHECKPOINT_FILE, "w", encoding="utf-8") as f:
                        json.dump(checkpoint_data, f)  # 保存所有信息
                    logging.info("已更新检查点文件")
                    break

                logging.info(f"当前处理校对块：{i + 1}/{len(translated_chunks)}")
                try:
                    corrected_chunk = check_translation(text, chunk, style, filename)  # 传递 chunk,风格，文件名
                    corrected_text += corrected_chunk

                    # 保存校对到临时文件
                    try:
                        with open(TEMP_CHECKED_FILE, "w", encoding="utf-8") as f:
                            f.write(corrected_text)  # 保存所有已校对的文本
                        logging.info(f"已保存校对块到临时文件")
                    except Exception as e:
                        logging.error(f"保存校对块到临时文件失败：{e}")
                        break
                except Exception as e:
                    logging.error(f"校对第{i}块发生错误：{e}")
                    pass  # 如果校对当前块发生错误，跳出循环

                start_chunk_index = i #更新校对起始位置

        except Exception as e:
            logging.error(f"校对 文本失败：{e}")
            corrected_text = translated_text if translated_text else text #校对失败，保持结果

         # 10. 全部完成后，保存到最终文件
        try:
            #移除 Word 文档格式
            cleaned_text = clean_xml_string(corrected_text)
            #  将换行符统一转换为 \n
            cleaned_text = cleaned_text.replace("\r\n", "\n").replace("\r", "\n")
             # 使用 codecs 模块以 UTF-8 编码写入文件
            with codecs.open(translated_file_path, "w", encoding="utf-8") as f:
                f.write(cleaned_text)
            logging.info(f"保存最终文件前，已校对文本长度：{len(cleaned_text)}")
            logging.info(f"翻译和校对完成，文件已保存到 {translated_file_path}")

             # 全部完成后，删除检查点文件
            #if os.path.exists(TEMP_CHECKPOINT_FILE):
             #    os.remove(TEMP_CHECKPOINT_FILE)
             #   logging.info("已删除检查点文件")

        except Exception as e:
            logging.error(f"保存最终文件失败: {e}, 错误类型：{type(e).__name__}, 错误内容：{e}")

        finally:
            is_thread_running = False
            if is_translating: #如果是因为暂停而跳出，则不清理
                logging.info("翻译或校对任务已暂停，未清理临时文件和标志文件")
            
    except Exception as e:
        logging.error(f"翻译过程中发生错误: {e}")
        is_thread_running = False
         #清理 标志文件
        if os.path.exists(IS_UPLOADING_FLAG):
            os.remove(IS_UPLOADING_FLAG)
        if os.path.exists(IS_CHECKING_FLAG):
             os.remove(IS_CHECKING_FLAG)
   

def start_translation_thread(file_path, style, temperature):
    global is_thread_running
    if not is_thread_running: # 避免重复启动线程
        is_thread_running = True
        executor.submit(translate_document, file_path, style, temperature)  # 提交任务到线程池
    else:
        logging.warning("翻译线程已经在运行，请稍后再试")

# 请求模型，用于接收前端数据
class TranslationRequest(BaseModel):
    file_path: str


app = FastAPI()

# 明确指定static文件夹路径
static_folder_path = r"D:\gemini\geminitranslator\static"

# 确保 'static' 文件夹存在
if not os.path.exists(static_folder_path):
    raise RuntimeError(f"Directory '{static_folder_path}' does not exist")

# 将 'static' 文件夹中的内容提供为静态文件
app.mount("/static", StaticFiles(directory=static_folder_path), name="static")

# 配置根路径返回 index.html
@app.get("/", response_class=HTMLResponse)
def read_html():
    try:
        # 明确指定 index.html 的路径
        with open(os.path.join(static_folder_path, "index.html"), "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content)  # 返回 index.html 内容
    except Exception as e:
        return f"Error: {e}"

@app.get("/last_translation_params")
async def get_last_translation_params():
    """获取上次翻译的参数"""
    logging.info(f"尝试获取上次翻译的参数，全局文件路径：{global_file_path}, 风格：{global_style}, 温度：{global_temperature}")
    return {
        "file_path": global_file_path,
        "style": global_style,
        "temperature": global_temperature,
    }

# 翻译线程控制
translation_thread = None

@app.post("/start_translation")
def start_translation(file_path: str = Form(...), style: str = Form(...), temperature: float = Form(...)):
    logging.info(f"Start Translation: Received file_path from form: {file_path}") # Added logging
    global translation_thread, is_translating, global_file_path, global_style, global_temperature
    if is_thread_running:
        return {"message": "翻译任务正在进行中，请稍等。"}

    is_translating = True

    # 保存到全局变量
    global_file_path = file_path
    global_style = style
    global_temperature = temperature

    save_translation_params() # 保存参数到文件

    start_translation_thread(file_path, style, temperature)
    return {"message": "翻译任务已启动。"}

@app.post("/pause")
def pause_translation(file_path: str = Form(...), style: str = Form(...), temperature: float = Form(...)):
    logging.info(f"Pause Translation: Received file_path from form: {file_path}") # Added logging
    global translation_thread, is_translating, global_file_path, global_style, global_temperature
    is_translating = False  # 确保暂停时设置 is_translating 为 False

    # 保存到全局变量
    global_file_path = file_path
    global_style = style
    global_temperature = temperature

    save_translation_params() # 保存参数到文件

    return {"message": "当前任务已暂停"}

@app.post("/resume")
def resume_translation():
    global translation_thread, is_translating, global_file_path, global_style, global_temperature

    logging.info(f"Resume: Attempting to resume translation...")

    if is_thread_running:
        return {"message": "翻译任务正在进行中，请稍等。"}

    # 尝试加载上次的翻译参数
    loaded_params = load_translation_params()
    if loaded_params:
        global global_file_path, global_style, global_temperature
        global_file_path = loaded_params.get("file_path", "")
        global_style = loaded_params.get("style", "")
        global_temperature = loaded_params.get("temperature", "") # 默认值
        logging.info(f"Resume: Loaded params - global_file_path={global_file_path}, style={global_style}, temp={global_temperature}")

    else:
        logging.warning("Resume: Failed to load last translation params, using global variables or restarting")
        if not global_file_path: # 如果全局变量也没有文件路径，则无法恢复
            return {"message": "无法恢复翻译，请重新选择文件开始翻译"}

    # 1. 检查是否有校对临时文件，如果存在，则说明上次任务是在校对阶段结束的
    filename = os.path.splitext(os.path.basename(global_file_path))[0]
    TEMP_CHECKED_FILE = os.path.join(TEMP_DIR, f"{filename}_temp_checked.txt")
    if os.path.exists(TEMP_CHECKED_FILE):
        # 如果有校对临时文件，提示用户选择
        return {"message": "检测到上次有未完成的校对任务，请点击 “继续上次任务” 按钮以继续校对。"}
    # 2. 检查是否有翻译临时文件，如果存在，则说明上次任务是在翻译阶段结束的
    filename = os.path.splitext(os.path.basename(global_file_path))[0]
    TEMP_TRANSLATED_FILE = os.path.join(TEMP_DIR, f"{filename}_temp_translated.txt")
    if os.path.exists(TEMP_TRANSLATED_FILE):
        # 如果有翻译临时文件，提示用户选择
        return {"message": "检测到上次有未完成的翻译任务，请点击 “继续上次任务” 按钮以继续翻译。"}

    # 3. 如果都没有临时文件，则说明需要从头开始翻译
    return {"message": "没有检测到未完成的翻译或校对任务，请重新选择文件开始翻译。"}

@app.post("/stop")
def stop_translation(file_path: str = Form(...), style: str = Form(...), temperature: float = Form(...)):
    global translation_thread, is_translating, global_file_path, global_style, global_temperature

    logging.info(f"Stop Translation: Received file_path from form: {file_path}") # Added logging
    is_translating = False  # 确保停止时设置 is_translating 为 False

    # 保存到全局变量
    global_file_path = file_path
    global_style = style
    global_temperature = temperature

    save_translation_params() # 保存参数到文件

    # 清理临时文件和检查点文件
    filename = os.path.splitext(os.path.basename(file_path))[0]
    TEMP_INPUT_FILE = os.path.join(TEMP_DIR, f"{filename}_temp_input.txt")
    TEMP_TRANSLATED_FILE = os.path.join(TEMP_DIR, f"{filename}_temp_translated.txt")
    TEMP_CHECKED_FILE =  os.path.join(TEMP_DIR, f"{filename}_temp_checked.txt")
    TEMP_CHECKPOINT_FILE = os.path.join(TEMP_DIR, f"{filename}_checkpoint.json")
    IS_UPLOADING_FLAG = os.path.join(TEMP_DIR, "is_uploading.flag")
    IS_CHECKING_FLAG = os.path.join(TEMP_DIR, "is_checking.flag")

    try:
        if os.path.exists(TEMP_INPUT_FILE):
            os.remove(TEMP_INPUT_FILE)
            logging.info("已删除临时输入文件")
        if os.path.exists(TEMP_CHECKPOINT_FILE):
            os.remove(TEMP_CHECKPOINT_FILE)
            logging.info("已删除检查点文件")
        if os.path.exists(IS_UPLOADING_FLAG):
            os.remove(IS_UPLOADING_FLAG)
            logging.info("已删除上传标志文件")
        if os.path.exists(IS_CHECKING_FLAG):
            os.remove(IS_CHECKING_FLAG)
            logging.info("已删除校对标志文件")

        logging.info("已清理临时文件和标志文件")
    except Exception as e:
        logging.error(f"清理临时文件失败: {e}")

    return {"message": "翻译任务已停止，所有进度已被清除。"}

PARAM_FILE = "D:/gemini/gemini-translated/temp/last_translation_params.json" # Define a file to store parameters

def save_translation_params():
    """保存翻译参数到文件"""
    params = {
        "file_path": os.path.normpath(global_file_path) if global_file_path else "", # Normalize path
        "style": global_style,
        "temperature": global_temperature,
    }
    os.makedirs(os.path.dirname(PARAM_FILE), exist_ok=True)
    try:
        with open(PARAM_FILE, 'w', encoding='utf-8') as f:
            json.dump(params, f, ensure_ascii=False, indent=4)
        logging.info(f"Save Params: 已保存翻译参数到: {PARAM_FILE}, 内容: {params}")
    except Exception as e:
        logging.error(f"Save Params: 保存翻译参数失败: {e}")

def load_translation_params():
    """从文件加载翻译参数"""
    try:
        if os.path.exists(PARAM_FILE): # Check if the file exists before trying to open it
            with open(PARAM_FILE, 'r', encoding='utf-8') as f: # 指定编码
                params = json.load(f)
                return params
        else:
            logging.warning(f"参数文件不存在: {PARAM_FILE}")
            return None # or return default values if needed
    except Exception as e:
        logging.error(f"加载翻译参数失败: {e}")
        return None

if __name__ == "__main__":
    # *** 全局声明移动到此 ***

    # 自动打开默认浏览器
    webbrowser.open("http://127.0.0.1:8001")

    # ***  新增：程序启动时自动尝试恢复翻译/校对  ***
    logging.info("程序启动，检查是否有未完成的翻译/校对任务...")
    loaded_params = load_translation_params()  # 尝试加载上次的翻译参数

    if loaded_params and loaded_params.get("file_path"):  # 如果加载成功且 file_path 不为空, 则尝试恢复
        global_file_path = loaded_params.get("file_path", "")
        global_style = loaded_params.get("style", "")
        global_temperature = loaded_params.get("temperature", "")

        logging.info(f"检测到上次未完成的任务, 文件路径={global_file_path}")

        # 检查是否有校对临时文件
        filename = os.path.splitext(os.path.basename(global_file_path))[0]
        TEMP_CHECKED_FILE = os.path.join(TEMP_DIR, f"{filename}_temp_checked.txt")
        if os.path.exists(TEMP_CHECKED_FILE):
            logging.info("检测到上次有未完成的校对任务, 请选择重新校对或放弃")
        # 检查是否有翻译临时文件
        filename = os.path.splitext(os.path.basename(global_file_path))[0]
        TEMP_TRANSLATED_FILE = os.path.join(TEMP_DIR, f"{filename}_temp_translated.txt")
        if os.path.exists(TEMP_TRANSLATED_FILE):
            logging.info("检测到上次有未完成的翻译任务, 请选择重新翻译或放弃")
        else:
            logging.info("没有检测到未完成的翻译/校对任务, 等待用户操作")
    else:
        logging.info("未检测到上次未完成的翻译/校对任务, 等待用户操作")
    # ***  自动恢复逻辑结束  ***

    uvicorn.run(app, host="0.0.0.0", port=8001)