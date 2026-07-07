import datetime
import logging
import os
import platform
import re
import sys

import colorlog
import json5
import requests
from requests.exceptions import ConnectionError, ReadTimeout

import utils.static as static
from steampy.exceptions import ApiException, ConfirmationExpected, EmptyResponse, InvalidCredentials, InvalidResponse, SteamError
from utils.static import BUILD_INFO, CONFIG_FILE_PATH, CURRENT_VERSION, LOGS_FOLDER, STEAM_ERROR_CODES

sensitive_data = []
sensitive_keys = [
    "ApiKey",
    "TradeLink",
    "JoinTime",
    "NickName",
    "access_token",
    "refresh_token",
    "shared_secret",
    "identity_secret",
    "steam_password",
    "app_key",
    "app-key",
    "csrf_token",
    "session",
    "trade_url",
    "TransactionUrl",
    "RealName",
    "IdCard",
]

if not os.path.exists(LOGS_FOLDER):
    os.mkdir(LOGS_FOLDER)


class LogFilter(logging.Filter):
    @staticmethod
    def add_sensitive_data(data):
        sensitive_data.append(data)

    def filter(self, record):
        if not isinstance(record.msg, str):
            return True
        for sensitive in sensitive_data:
            record.msg = record.msg.replace(sensitive, "*" * len(sensitive))

        def mask_value(value):
            return "*" * len(value)

        # 处理 JSON 数据中的敏感信息
        for key in sensitive_keys:
            pattern = rf'"{key}"\s*:\s*("(.*?)"|(\d+)|(true|false|null))'

            def replace_match(match):
                if match.group(2):  # 如果匹配到的是带引号的字符串
                    return f'"{key}": "{mask_value(match.group(2))}"'
                elif match.group(3):  # 如果匹配到的是数字
                    return f'"{key}": {mask_value(match.group(3))}'
                elif match.group(4):  # 如果匹配到的是true, false或null
                    return f'"{key}": {mask_value(match.group(4))}'

            record.msg = re.sub(pattern, replace_match, record.msg, flags=re.IGNORECASE)  # type: ignore

        # 处理 URL 参数中的敏感信息
        for key in sensitive_keys:
            pattern = rf"({key}=)([^&\s]+)"

            def replace_url_match(match):
                return f"{match.group(1)}{mask_value(match.group(2))}"

            record.msg = re.sub(pattern, replace_url_match, record.msg, flags=re.IGNORECASE)

        return True


log_retention_days = None
log_level = None
try:
    with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
        config = json5.loads(f.read())
        if isinstance(config, dict):
            log_level = str(config.get("log_level", "DEBUG")).upper()
            log_retention_days = int(config.get("log_retention_days", 7))
except Exception as e:
    pass

if log_retention_days:
    for log_file in os.listdir(LOGS_FOLDER):
        if log_file.endswith(".log"):
            log_file_path = os.path.join(LOGS_FOLDER, log_file)
            if (datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(log_file_path))) > datetime.timedelta(days=log_retention_days):
                os.remove(log_file_path)

logger = logging.getLogger()
logger.setLevel(0)
s_handler = logging.StreamHandler()
s_handler.setLevel(logging.INFO)
log_formatter_colored = colorlog.ColoredFormatter(
    fmt="%(log_color)s[%(asctime)s] - %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    log_colors={"DEBUG": "cyan", "INFO": "green", "WARNING": "yellow", "ERROR": "red", "CRITICAL": "bold_red"},
)
s_handler.setFormatter(log_formatter_colored)
log_formatter = logging.Formatter("[%(asctime)s] - %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S")
logger.addHandler(s_handler)
f_handler = logging.FileHandler(os.path.join(LOGS_FOLDER, datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S") + ".log"), encoding="utf-8")
if log_level and log_level.isdigit():
    f_handler.setLevel(int(log_level))
elif log_level == "INFO":
    f_handler.setLevel(logging.INFO)
elif log_level == "WARNING":
    f_handler.setLevel(logging.WARNING)
elif log_level == "ERROR":
    f_handler.setLevel(logging.ERROR)
else:
    f_handler.setLevel(logging.DEBUG)
f_handler.setFormatter(log_formatter)
logger.addHandler(f_handler)
logger.addFilter(LogFilter())
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
logging.getLogger("apprise").setLevel(logging.WARNING)
logger.debug(f"Steamauto {CURRENT_VERSION} started")
logger.debug(f"Running on {platform.system()} {platform.release()}({platform.version()})")
logger.debug(f"Python version: {os.sys.version}")  # type: ignore
logger.debug(f"Build info: {BUILD_INFO}")
logger.debug(f"Attributes check: _MEIPASS: {hasattr(sys, '_MEIPASS')}, frozen: {hasattr(sys, 'frozen')}")


def handle_caught_exception(e: Exception, prefix: str = "", known: bool = False):
    plogger = logger
    if prefix and not prefix.endswith(" "):
        plogger = PluginLogger(prefix)
    if (not static.is_latest_version) and not known:
        plogger.warning("当前Steamauto版本可能不是最新版本！请在更新到新版本后再次尝试！")
    logger.debug(e, exc_info=True)

    if isinstance(e, KeyboardInterrupt):
        plogger.info("检测到键盘中断,程序即将退出...")
        exit(0)
    elif isinstance(e, SystemExit):
        plogger.info("检测到系统退出请求,程序即将退出...")
        exit(0)
    elif isinstance(e, requests.exceptions.SSLError):
        plogger.error("梯子问题, 请更换梯子")
    elif isinstance(e, EmptyResponse):
        plogger.error("Steam返回空响应, 可能是IP受到Steam风控, 请更换IP或稍后再试")
    elif isinstance(e, requests.exceptions.ProxyError):
        plogger.error("代理异常。建议关闭代理。如果你连接Steam有困难，可单独打开配置文件内的Steam代理功能。")
    elif isinstance(e, (ConnectionError, ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError, ReadTimeout, InvalidResponse)):
        plogger.error("网络异常, 请检查网络连接")
        plogger.error("这个错误可能是由于代理或VPN引起的, 本软件可不使用代理或任何VPN")
        plogger.error("如果你正在使用代理或VPN, 请尝试关闭后重启软件")
        plogger.error("如果你没有使用代理或VPN, 请检查网络连接")
    elif isinstance(e, InvalidCredentials):
        if "Invalid API key" in str(e):
            plogger.error("Steam access_token/API 会话已失效，正在或需要重新登录")
            plogger.error(str(e))
        else:
            plogger.error("Steam 登录凭据无效，请检查账号密码或mafile是否正确")
            plogger.error(str(e))
    elif isinstance(e, ConfirmationExpected):
        plogger.error("Steam Session已经过期, 请删除session文件夹并重启Steamauto")
    elif isinstance(e, SystemError):
        plogger.error("无法连接至Steam，请检查Steam账户状态、网络连接、或重启Steamauto")
    elif isinstance(e, SteamError):
        plogger.error("Steam 异常, 异常id:" + str(e.error_code) + ", 异常信息:" + STEAM_ERROR_CODES.get(e.error_code, "未知Steam错误"))
    elif isinstance(e, ApiException):
        if "Invalid trade offer state" in str(e):
            if "Canceled" in str(e):
                plogger.error("交易已取消，无法接受报价")
            elif "Accepted" in str(e):
                plogger.error("交易已接受，无法重复操作")
            else:
                plogger.error("交易状态异常，无法接受报价，异常信息：" + str(e))
        else:
            plogger.error("Steam API 异常, 异常信息: " + str(e))
    else:
        if not known:
            plogger.error(
                f"当前Steamauto版本：{CURRENT_VERSION}\nPython版本：{os.sys.version}\n系统版本：{platform.system()} {platform.release()}({platform.version()})\n编译信息：{BUILD_INFO}\n"  # type: ignore
            )
            plogger.error("发生未知异常, 异常信息:" + str(e) + ", 异常类型:" + str(type(e)) + " 已记录至日志文件")

        if BUILD_INFO == "正在使用源码运行":
            plogger.error(e, exc_info=True)


class PluginLogger:
    def __init__(self, pluginName):
        if "[" and "]" not in pluginName:
            self.pluginName = f"[{pluginName}]"
        else:
            self.pluginName = pluginName

    def debug(self, msg, *args, **kwargs):
        logger.debug(f"{self.pluginName} {msg}", *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        logger.info(f"{self.pluginName} {msg}", *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        logger.warning(f"{self.pluginName} {msg}", *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        logger.error(f"{self.pluginName} {msg}", *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        logger.critical(f"{self.pluginName} {msg}", *args, **kwargs)

    def log(self, level, msg, *args, **kwargs):
        logger.log(level, f"{self.pluginName} {msg}", *args, **kwargs)
