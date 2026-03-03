"""
配置管理Web界面启动文件
提供Web配置界面功能，包括:
- 初始化Python路径
- 禁用字节码缓存
- 清理缓存文件
- 启动Web服务器
- 动态修改配置
"""
import os
import sys
import re
import logging
from flask import Flask, render_template, jsonify, request, send_from_directory, redirect, url_for, session, g
import importlib
import json
from colorama import init, Fore, Style
from werkzeug.utils import secure_filename
from typing import Dict, Any, List
import psutil
import subprocess
import threading
from src.autoupdate.updater import Updater
import requests
import time
from queue import Queue
import datetime
from logging.config import dictConfig
import shutil
import signal
import atexit
import socket
import webbrowser
import hashlib
import secrets
from datetime import timedelta
from src.utils.console import print_status
from src.avatar_manager import avatar_manager
from src.webui.routes.avatar import avatar_bp
import ctypes

# Windows 专属导入（进程管理用，与微信无关）
if sys.platform.startswith('win'):
    try:
        import win32api
        import win32con
        import win32job
        import win32process
        _WIN32_AVAILABLE = True
    except ImportError:
        _WIN32_AVAILABLE = False
else:
    _WIN32_AVAILABLE = False

# 全局变量
bot_process = None
bot_start_time = None
bot_logs = Queue(maxsize=1000)
job_object = None

# 配置日志
dictConfig({
    'version': 1,
    'formatters': {
        'default': {
            'format': '[%(asctime)s] %(levelname)s: %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'default',
            'level': 'INFO'
        }
    },
    'root': {
        'level': 'INFO',
        'handlers': ['console']
    },
    'loggers': {
        'werkzeug': {
            'level': 'ERROR',
            'handlers': ['console'],
            'propagate': False
        }
    }
})

logger = logging.getLogger(__name__)
init()

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(ROOT_DIR)

config_path = os.path.join(ROOT_DIR, 'data/config/config.json')

sys.dont_write_bytecode = True

templates_dir = os.path.join(ROOT_DIR, 'src/webui/templates')
static_dir = os.path.join(ROOT_DIR, 'src/webui/static')

os.makedirs(templates_dir, exist_ok=True)
os.makedirs(static_dir, exist_ok=True)
os.makedirs(os.path.join(static_dir, 'js'), exist_ok=True)
os.makedirs(os.path.join(static_dir, 'css'), exist_ok=True)

app = Flask(__name__, template_folder=templates_dir, static_folder=static_dir)
app.config['UPLOAD_FOLDER'] = os.path.join(ROOT_DIR, 'src/webui/background_image')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

app.secret_key = secrets.token_hex(16)

try:
    app.register_blueprint(avatar_manager)
    app.register_blueprint(avatar_bp)
    logger.debug("成功注册蓝图组件")
except Exception as e:
    logger.error(f"注册蓝图组件失败: {str(e)}")

from src.autoupdate.updater import Updater

def check_cloud_updates_on_startup():
    try:
        from src.autoupdate.updater import check_cloud_info
        logger.info("应用启动时检查云端更新...")
        check_cloud_info()
        logger.info("云端更新检查完成")
        try:
            from src.autoupdate.core.manager import get_manager
            manager = get_manager()
            manager.check_and_process_updates()
            logger.info("公告数据处理完成，将在Web页面显示")
        except Exception as announcement_error:
            logger.error(f"公告处理失败: {announcement_error}")
    except Exception as e:
        logger.error(f"检查云端更新失败: {e}")

update_thread = threading.Thread(target=check_cloud_updates_on_startup)
update_thread.daemon = True
update_thread.start()


def get_available_avatars() -> List[str]:
    """获取可用的人设目录列表"""
    avatar_base_dir = os.path.join(ROOT_DIR, "data/avatars")
    if not os.path.exists(avatar_base_dir):
        os.makedirs(avatar_base_dir, exist_ok=True)
        return []

    avatars = []
    for item in os.listdir(avatar_base_dir):
        avatar_dir = os.path.join(avatar_base_dir, item)
        if os.path.isdir(avatar_dir):
            avatar_md_path = os.path.join(avatar_dir, "avatar.md")
            emojis_dir = os.path.join(avatar_dir, "emojis")

            if not os.path.exists(emojis_dir):
                os.makedirs(emojis_dir, exist_ok=True)

            if not os.path.exists(avatar_md_path):
                with open(avatar_md_path, 'w', encoding='utf-8') as f:
                    f.write("# 任务\n请在此处描述角色的任务和目标\n\n# 角色\n请在此处描述角色的基本信息\n\n"
                            "# 外表\n请在此处描述角色的外表特征\n\n# 经历\n请在此处描述角色的经历和背景故事\n\n"
                            "# 性格\n请在此处描述角色的性格特点\n\n# 经典台词\n请在此处列出角色的经典台词\n\n"
                            "# 喜好\n请在此处描述角色的喜好\n\n# 备注\n其他需要补充的信息")

            if os.path.exists(avatar_md_path) and os.path.exists(emojis_dir):
                avatars.append(f"data/avatars/{item}")

    if not avatars:
        default_avatar = "MONO"
        default_dir = os.path.join(avatar_base_dir, default_avatar)
        os.makedirs(default_dir, exist_ok=True)
        os.makedirs(os.path.join(default_dir, "emojis"), exist_ok=True)
        with open(os.path.join(default_dir, "avatar.md"), 'w', encoding='utf-8') as f:
            f.write("# 任务\n作为一个温柔体贴的虚拟助手，为用户提供陪伴和帮助\n\n"
                    "# 角色\n名字: MONO\n身份: AI助手\n\n# 外表\n清新甜美的少女形象\n\n"
                    "# 经历\n被创造出来陪伴用户\n\n# 性格\n温柔、体贴、善解人意\n\n"
                    "# 经典台词\n\"我会一直陪着你的~\"\n\"今天过得怎么样呀？\"\n\"需要我做什么呢？\"\n\n"
                    "# 喜好\n喜欢和用户聊天\n喜欢分享知识\n\n# 备注\n默认人设")
        avatars.append(f"data/avatars/{default_avatar}")

    return avatars


def parse_config_groups() -> Dict[str, Dict[str, Any]]:
    """解析配置文件，将配置项按组分类"""
    from data.config import config

    try:
        config_groups = {
            "Bot 配置": {},          # ← 新增，替代原微信用户列表
            "基础配置": {},
            "TTS 服务配置": {},
            "图像识别API配置": {},
            "意图识别API配置": {},
            "主动消息配置": {},
            "消息配置": {},
            "人设配置": {},
            "网络搜索配置": {},
            "世界书": {}
        }

        # ── Bot 配置（替代原微信监听列表）──────────────────────────────────────────
        config_groups["Bot 配置"].update({
            "BOT_TOKEN": {
                "value": getattr(getattr(config, 'bot', None), 'token', ''),
                "description": "Telegram Bot Token（通过 @BotFather 获取）",
                "is_secret": True,
            },
            "BOT_NAME": {
                "value": getattr(getattr(config, 'bot', None), 'name', ''),
                "description": "Bot 的 Telegram 用户名（不含 @，留空则启动时自动获取）",
            },
            "TELEGRAM_CHAT_IDS": {
                "value": getattr(getattr(config, 'user', None), 'telegram_chat_ids', []),
                "description": "允许 Bot 响应的 Chat ID 列表（私聊为正整数，群聊为负整数；留空则响应所有私聊）",
            },
            "PROXY_URL": {
                "value": getattr(getattr(config, 'bot', None), 'proxy_url', ''),
                "description": "Telegram API 代理地址（如 http://127.0.0.1:1080 或 socks5://127.0.0.1:1080），留空表示不使用代理",
                "type": "string",
            },
            "GROUP_CHAT_CONFIG": {
                "value": [
                    {
                        "id": item.id,
                        "groupName": item.group_name,
                        "avatar": item.avatar,
                        "triggers": item.triggers,
                        "enableAtTrigger": item.enable_at_trigger
                    } for item in config.user.group_chat_config
                ],
                "description": "群聊配置列表（为不同群聊配置专用人设和触发词）",
            },
        })

        # ── 基础配置 ───────────────────────────────────────────────────────────────
        config_groups["基础配置"].update({
            "DEEPSEEK_BASE_URL": {
                "value": config.llm.base_url,
                "description": "API注册地址",
            },
            "MODEL": {"value": config.llm.model, "description": "AI模型选择"},
            "DEEPSEEK_API_KEY": {
                "value": config.llm.api_key,
                "description": "API密钥",
            },
            "MAX_TOKEN": {
                "value": config.llm.max_tokens,
                "description": "回复最大token数",
                "type": "number",
            },
            "TEMPERATURE": {
                "value": float(config.llm.temperature),
                "type": "number",
                "description": "温度参数",
                "min": 0.0,
                "max": 1.7,
            },
            "AUTO_MODEL_SWITCH": {
                "value": config.llm.auto_model_switch,
                "type": "boolean",
                "description": "自动切换模型"
            },
        })

        # ── TTS 服务配置 ───────────────────────────────────────────────────────────
        config_groups["TTS 服务配置"].update({
            "TTS_API_KEY": {
                "value": config.media.text_to_speech.tts_api_key,
                "description": "Fish Audio API 密钥"
            },
            "TTS_MODEL_ID": {
                "value": config.media.text_to_speech.tts_model_id,
                "description": "进行 TTS 的模型 ID"
            }
        })

        # ── 图像识别API配置 ────────────────────────────────────────────────────────
        config_groups["图像识别API配置"].update({
            "VISION_BASE_URL": {
                "value": config.media.image_recognition.base_url,
                "description": "服务地址",
                "has_provider_options": True
            },
            "VISION_API_KEY": {
                "value": config.media.image_recognition.api_key,
                "description": "API密钥",
            },
            "VISION_MODEL": {
                "value": config.media.image_recognition.model,
                "description": "模型名称",
                "has_model_options": True
            },
            "VISION_TEMPERATURE": {
                "value": float(config.media.image_recognition.temperature),
                "description": "温度参数",
                "type": "number",
                "min": 0.0,
                "max": 1.0
            }
        })

        # ── 意图识别API配置 ────────────────────────────────────────────────────────
        config_groups["意图识别API配置"].update({
            "INTENT_BASE_URL": {
                "value": config.intent_recognition.base_url,
                "description": "API注册地址",
                "has_provider_options": True
            },
            "INTENT_API_KEY": {
                "value": config.intent_recognition.api_key,
                "description": "API密钥",
            },
            "INTENT_MODEL": {
                "value": config.intent_recognition.model,
                "description": "AI模型选择",
                "has_model_options": True
            },
            "INTENT_TEMPERATURE": {
                "value": float(config.intent_recognition.temperature),
                "description": "温度参数",
                "type": "number",
                "min": 0.0,
                "max": 1.0
            }
        })

        # ── 主动消息配置 ───────────────────────────────────────────────────────────
        config_groups["主动消息配置"].update({
            "AUTO_MESSAGE": {
                "value": config.behavior.auto_message.content,
                "description": "自动消息内容",
            },
            "MIN_COUNTDOWN_HOURS": {
                "value": config.behavior.auto_message.min_hours,
                "description": "最小倒计时时间（小时）",
            },
            "MAX_COUNTDOWN_HOURS": {
                "value": config.behavior.auto_message.max_hours,
                "description": "最大倒计时时间（小时）",
            },
            "QUIET_TIME_START": {
                "value": config.behavior.quiet_time.start,
                "description": "安静时间开始",
            },
            "QUIET_TIME_END": {
                "value": config.behavior.quiet_time.end,
                "description": "安静时间结束",
            },
        })

        # ── 消息配置 ───────────────────────────────────────────────────────────────
        config_groups["消息配置"].update({
            "QUEUE_TIMEOUT": {
                "value": config.behavior.message_queue.timeout,
                "description": "消息队列等待时间（秒）",
                "type": "number",
                "min": 8,
                "max": 20
            }
        })

        # ── 人设配置 ───────────────────────────────────────────────────────────────
        available_avatars = get_available_avatars()
        config_groups["人设配置"].update({
            "MAX_GROUPS": {
                "value": config.behavior.context.max_groups,
                "description": "最大的上下文轮数",
            },
            "AVATAR_DIR": {
                "value": config.behavior.context.avatar_dir,
                "description": "人设目录（自动包含 avatar.md 和 emojis 目录）",
                "options": available_avatars,
                "type": "select"
            }
        })

        # ── 网络搜索配置 ───────────────────────────────────────────────────────────
        config_groups["网络搜索配置"].update({
            "NETWORK_SEARCH_ENABLED": {
                "value": config.network_search.search_enabled,
                "type": "boolean",
                "description": "启用网络搜索功能（仅支持Kouri API）",
            },
            "WEBLENS_ENABLED": {
                "value": config.network_search.weblens_enabled,
                "type": "boolean",
                "description": "启用网页内容提取功能（仅支持Kouri API）",
            },
            "NETWORK_SEARCH_API_KEY": {
                "value": config.network_search.api_key,
                "type": "string",
                "description": "Kouri API 密钥（留空则使用 LLM 设置中的 API 密钥）",
                "is_secret": True
            }
        })

        # ── 世界书 ─────────────────────────────────────────────────────────────────
        worldview = ""
        try:
            with open(os.path.join(ROOT_DIR, 'src/base/worldview.md'), 'r', encoding='utf-8') as f:
                worldview = f.read()
        except Exception as e:
            logger.error(f"读取世界观失败: {str(e)}")

        config_groups['世界书'] = {
            'worldview': {
                'value': worldview,
                'type': 'text',
                'description': '内容'
            }
        }

        # ── 定时任务配置 ───────────────────────────────────────────────────────────
        tasks = []
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                tasks = (config_data
                         .get('categories', {})
                         .get('schedule_settings', {})
                         .get('settings', {})
                         .get('tasks', {})
                         .get('value', []))
        except Exception as e:
            logger.error(f"读取任务数据失败: {str(e)}")

        config_groups['定时任务配置'] = {
            'tasks': {
                'value': tasks,
                'type': 'array',
                'description': '定时任务列表'
            }
        }

        return config_groups

    except Exception as e:
        logger.error(f"解析配置组失败: {str(e)}")
        return {}


@app.route('/')
def index():
    return redirect(url_for('dashboard'))


def load_config_file():
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载配置失败: {str(e)}")
        return {"categories": {}}


def save_config_file(config_data):
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        logger.error(f"保存配置失败: {str(e)}")
        return False


def reinitialize_tasks():
    logger.info("配置已更新，任务将在主程序下次启动时生效")
    return True


@app.route('/save', methods=['POST'])
def save_config():
    """保存配置"""
    try:
        if not request.is_json:
            return jsonify({"status": "error", "message": "请求Content-Type必须是application/json", "title": "错误"}), 415

        config_data = request.get_json()
        if not config_data:
            return jsonify({"status": "error", "message": "无效的JSON数据", "title": "错误"}), 400

        current_config = load_config_file()

        # 所有受理的配置键
        KNOWN_KEYS = {
            'TELEGRAM_CHAT_IDS', 'GROUP_CHAT_CONFIG', 'BOT_TOKEN', 'BOT_NAME',
            'DEEPSEEK_BASE_URL', 'MODEL', 'DEEPSEEK_API_KEY', 'MAX_TOKEN', 'TEMPERATURE', 'AUTO_MODEL_SWITCH',
            'VISION_API_KEY', 'VISION_BASE_URL', 'VISION_TEMPERATURE', 'VISION_MODEL',
            'INTENT_API_KEY', 'INTENT_BASE_URL', 'INTENT_MODEL', 'INTENT_TEMPERATURE',
            'IMAGE_MODEL', 'TEMP_IMAGE_DIR', 'AUTO_MESSAGE', 'MIN_COUNTDOWN_HOURS', 'MAX_COUNTDOWN_HOURS',
            'QUIET_TIME_START', 'QUIET_TIME_END', 'TTS_API_URL', 'VOICE_DIR', 'MAX_GROUPS', 'AVATAR_DIR',
            'QUEUE_TIMEOUT', 'NETWORK_SEARCH_ENABLED', 'WEBLENS_ENABLED', 'NETWORK_SEARCH_API_KEY',
            'NETWORK_SEARCH_BASE_URL', 'TTS_API_KEY', 'TTS_MODEL_ID',
        }

        for key, value in config_data.items():
            if key == 'TASKS':
                try:
                    tasks = value if isinstance(value, list) else (json.loads(value) if isinstance(value, str) else [])
                    cats = current_config.setdefault('categories', {})
                    ss = cats.setdefault('schedule_settings', {'title': '定时任务配置', 'settings': {}})
                    ss.setdefault('settings', {}).setdefault('tasks', {
                        'value': [], 'type': 'array', 'description': '定时任务列表'
                    })['value'] = tasks
                except Exception as e:
                    logger.error(f"处理定时任务配置失败: {str(e)}")
                    return jsonify({"status": "error", "message": f"处理定时任务配置失败: {str(e)}", "title": "错误"}), 400
            elif key in KNOWN_KEYS:
                update_config_value(current_config, key, value)
            elif key == 'WORLDVIEW':
                try:
                    with open(os.path.join(ROOT_DIR, 'src/base/worldview.md'), 'w', encoding='utf-8') as f:
                        f.write(value)
                except Exception as e:
                    logger.error(f"保存世界观配置失败: {str(e)}")
            else:
                logger.warning(f"未知的配置项: {key}")

        if not save_config_file(current_config):
            return jsonify({"status": "error", "message": "保存配置文件失败", "title": "错误"}), 500

        g.config_data = current_config
        return jsonify({"status": "success", "message": "✨ 配置已成功保存并生效", "title": "保存成功"})

    except Exception as e:
        logger.error(f"保存配置失败: {str(e)}")
        return jsonify({"status": "error", "message": f"保存失败: {str(e)}", "title": "错误"}), 500


def update_config_value(config_data, key, value):
    """更新配置值到正确的位置"""
    try:
        mapping = {
            # Bot 配置（替代原 LISTEN_LIST）
            'BOT_TOKEN':           ['categories', 'bot_settings', 'settings', 'token', 'value'],
            'BOT_NAME':            ['categories', 'bot_settings', 'settings', 'name', 'value'],
            'PROXY_URL':           ['categories', 'bot_settings', 'settings', 'proxy_url', 'value'],
            'TELEGRAM_CHAT_IDS':   ['categories', 'user_settings', 'settings', 'telegram_chat_ids', 'value'],
            'GROUP_CHAT_CONFIG':   ['categories', 'user_settings', 'settings', 'group_chat_config', 'value'],
            # LLM
            'DEEPSEEK_BASE_URL':   ['categories', 'llm_settings', 'settings', 'base_url', 'value'],
            'MODEL':               ['categories', 'llm_settings', 'settings', 'model', 'value'],
            'DEEPSEEK_API_KEY':    ['categories', 'llm_settings', 'settings', 'api_key', 'value'],
            'MAX_TOKEN':           ['categories', 'llm_settings', 'settings', 'max_tokens', 'value'],
            'TEMPERATURE':         ['categories', 'llm_settings', 'settings', 'temperature', 'value'],
            'AUTO_MODEL_SWITCH':   ['categories', 'llm_settings', 'settings', 'auto_model_switch', 'value'],
            # 媒体
            'VISION_API_KEY':      ['categories', 'media_settings', 'settings', 'image_recognition', 'api_key', 'value'],
            'VISION_BASE_URL':     ['categories', 'media_settings', 'settings', 'image_recognition', 'base_url', 'value'],
            'VISION_TEMPERATURE':  ['categories', 'media_settings', 'settings', 'image_recognition', 'temperature', 'value'],
            'VISION_MODEL':        ['categories', 'media_settings', 'settings', 'image_recognition', 'model', 'value'],
            'IMAGE_MODEL':         ['categories', 'media_settings', 'settings', 'image_generation', 'model', 'value'],
            'TEMP_IMAGE_DIR':      ['categories', 'media_settings', 'settings', 'image_generation', 'temp_dir', 'value'],
            'TTS_API_KEY':         ['categories', 'media_settings', 'settings', 'text_to_speech', 'tts_api_key', 'value'],
            'TTS_MODEL_ID':        ['categories', 'media_settings', 'settings', 'text_to_speech', 'tts_model_id', 'value'],
            'TTS_API_URL':         ['categories', 'media_settings', 'settings', 'text_to_speech', 'tts_api_url', 'value'],
            'VOICE_DIR':           ['categories', 'media_settings', 'settings', 'text_to_speech', 'voice_dir', 'value'],
            # 网络搜索
            'NETWORK_SEARCH_ENABLED':  ['categories', 'network_search_settings', 'settings', 'search_enabled', 'value'],
            'WEBLENS_ENABLED':         ['categories', 'network_search_settings', 'settings', 'weblens_enabled', 'value'],
            'NETWORK_SEARCH_API_KEY':  ['categories', 'network_search_settings', 'settings', 'api_key', 'value'],
            'NETWORK_SEARCH_BASE_URL': ['categories', 'network_search_settings', 'settings', 'base_url', 'value'],
            # 意图识别
            'INTENT_API_KEY':      ['categories', 'intent_recognition_settings', 'settings', 'api_key', 'value'],
            'INTENT_BASE_URL':     ['categories', 'intent_recognition_settings', 'settings', 'base_url', 'value'],
            'INTENT_MODEL':        ['categories', 'intent_recognition_settings', 'settings', 'model', 'value'],
            'INTENT_TEMPERATURE':  ['categories', 'intent_recognition_settings', 'settings', 'temperature', 'value'],
            # 行为
            'AUTO_MESSAGE':          ['categories', 'behavior_settings', 'settings', 'auto_message', 'content', 'value'],
            'MIN_COUNTDOWN_HOURS':   ['categories', 'behavior_settings', 'settings', 'auto_message', 'countdown', 'min_hours', 'value'],
            'MAX_COUNTDOWN_HOURS':   ['categories', 'behavior_settings', 'settings', 'auto_message', 'countdown', 'max_hours', 'value'],
            'QUIET_TIME_START':      ['categories', 'behavior_settings', 'settings', 'quiet_time', 'start', 'value'],
            'QUIET_TIME_END':        ['categories', 'behavior_settings', 'settings', 'quiet_time', 'end', 'value'],
            'QUEUE_TIMEOUT':         ['categories', 'behavior_settings', 'settings', 'message_queue', 'timeout', 'value'],
            'MAX_GROUPS':            ['categories', 'behavior_settings', 'settings', 'context', 'max_groups', 'value'],
            'AVATAR_DIR':            ['categories', 'behavior_settings', 'settings', 'context', 'avatar_dir', 'value'],
        }

        if key not in mapping:
            logger.warning(f"未知的配置项: {key}")
            return

        path = mapping[key]

        # ── 类型预处理 ──────────────────────────────────────────────────────────────

        # Chat ID 列表（逗号分隔字符串 → list[str]）
        if key == 'TELEGRAM_CHAT_IDS' and isinstance(value, str):
            value = [v.strip() for v in value.split(',') if v.strip()]

        # GROUP_CHAT_CONFIG（JSON 字符串 → list）
        elif key == 'GROUP_CHAT_CONFIG':
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except Exception:
                    value = []
            elif not isinstance(value, list):
                value = []

        # AUTO_MODEL_SWITCH 布尔
        elif key == 'AUTO_MODEL_SWITCH':
            value = True if value == 'on' else bool(value)

        # 数字类型
        elif isinstance(value, str) and key in {
            'MAX_TOKEN', 'TEMPERATURE', 'VISION_TEMPERATURE', 'INTENT_TEMPERATURE',
            'MIN_COUNTDOWN_HOURS', 'MAX_COUNTDOWN_HOURS', 'MAX_GROUPS', 'QUEUE_TIMEOUT'
        }:
            try:
                value = float(value)
                if key in {'MAX_TOKEN', 'MAX_GROUPS', 'QUEUE_TIMEOUT'}:
                    value = int(value)
            except ValueError:
                pass

        # 布尔类型
        elif key in {'NETWORK_SEARCH_ENABLED', 'WEBLENS_ENABLED'}:
            if isinstance(value, str):
                value = value.lower() == 'true'
            value = bool(value)

        # ── 写入路径 ────────────────────────────────────────────────────────────────
        current = config_data
        for part in path[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[path[-1]] = value

    except Exception as e:
        logger.error(f"更新配置值失败 {key}: {str(e)}")


@app.route('/upload_background', methods=['POST'])
def upload_background():
    if 'background' not in request.files:
        return jsonify({"status": "error", "message": "没有选择文件"})
    file = request.files['background']
    if not file.filename:
        return jsonify({"status": "error", "message": "文件名无效"})
    filename = secure_filename(file.filename)
    for old_file in os.listdir(app.config['UPLOAD_FOLDER']):
        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], old_file))
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    return jsonify({"status": "success", "message": "背景图片已更新", "path": f"/background_image/{filename}"})


@app.route('/background_image/<filename>')
def background_image(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/get_background')
def get_background():
    try:
        files = os.listdir(app.config['UPLOAD_FOLDER'])
        if files:
            return jsonify({"status": "success", "path": f"/background_image/{files[0]}"})
        return jsonify({"status": "success", "path": None})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.before_request
def load_config():
    try:
        g.config_data = load_config_file()
    except Exception as e:
        logger.error(f"加载配置失败: {str(e)}")


@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    show_announcement = False
    try:
        from src.autoupdate.announcement import has_unread_announcement
        show_announcement = has_unread_announcement()
    except Exception as e:
        logger.warning(f"检查公告状态失败: {e}")

    config_groups = g.config_data.get('categories', {})
    return render_template(
        'dashboard.html',
        is_local=is_local_network(),
        active_page='dashboard',
        config_groups=config_groups,
        show_announcement=show_announcement
    )


@app.route('/system_info')
def system_info():
    try:
        if not hasattr(system_info, 'last_bytes'):
            system_info.last_bytes = {'sent': 0, 'recv': 0, 'time': time.time()}

        cpu_percent = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        net = psutil.net_io_counters()

        current_time = time.time()
        time_delta = current_time - system_info.last_bytes['time']
        upload_speed = (net.bytes_sent - system_info.last_bytes['sent']) / time_delta / 1024
        download_speed = (net.bytes_recv - system_info.last_bytes['recv']) / time_delta / 1024
        system_info.last_bytes = {'sent': net.bytes_sent, 'recv': net.bytes_recv, 'time': current_time}

        return jsonify({
            'cpu': cpu_percent,
            'memory': {'total': round(memory.total / (1024**3), 2), 'used': round(memory.used / (1024**3), 2), 'percent': memory.percent},
            'disk': {'total': round(disk.total / (1024**3), 2), 'used': round(disk.used / (1024**3), 2), 'percent': disk.percent},
            'network': {'upload': round(upload_speed, 2), 'download': round(download_speed, 2)}
        })
    except Exception as e:
        logger.error(f"获取系统信息失败: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/check_update')
def check_update():
    try:
        updater = Updater()
        result = updater.check_for_updates()
        return jsonify({
            'status': 'success',
            'has_update': result.get('has_update', False),
            'console_output': result['output'],
            'update_info': result if result.get('has_update') else None,
            'wait_input': False
        })
    except Exception as e:
        logger.error(f"检查更新失败: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'has_update': False, 'console_output': f'检查更新失败: {str(e)}'})


@app.route('/confirm_update', methods=['POST'])
def confirm_update():
    try:
        choice = (request.json or {}).get('choice', '').lower()
        if choice in ('y', 'yes', '是', '确认', '确定'):
            updater = Updater()
            result = updater.update(callback=lambda msg: logger.info(f"更新进度: {msg}"))
            return jsonify({
                'status': 'success' if result['success'] else 'error',
                'console_output': result.get('message', '更新过程出现未知错误')
            })
        return jsonify({'status': 'success', 'console_output': '用户取消更新'})
    except Exception as e:
        logger.error(f"更新失败: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'console_output': f'更新失败: {str(e)}'})


update_progress_logs = []
update_in_progress = False


@app.route('/execute_update', methods=['POST'])
def execute_update():
    global update_progress_logs, update_in_progress
    if update_in_progress:
        return jsonify({'status': 'error', 'message': '更新正在进行中，请稍候...'})
    try:
        update_in_progress = True
        update_progress_logs = []

        def progress_callback(msg):
            logger.info(f"更新进度: {msg}")
            update_progress_logs.append({'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'), 'message': msg})

        progress_callback("Starting update process...")
        updater = Updater()
        result = updater.update(callback=progress_callback)
        final_message = result.get('message', '更新过程出现未知错误')
        progress_callback(f"Update completed: {final_message}")
        return jsonify({
            'status': 'success' if result['success'] else 'error',
            'message': final_message,
            'restart_required': result.get('restart_required', False)
        })
    except Exception as e:
        error_msg = f'更新失败: {str(e)}'
        logger.error(error_msg, exc_info=True)
        update_progress_logs.append({'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'), 'message': error_msg})
        return jsonify({'status': 'error', 'message': error_msg})
    finally:
        update_in_progress = False


@app.route('/update_progress')
def get_update_progress():
    return jsonify({'logs': update_progress_logs, 'in_progress': update_in_progress})


def start_bot_process():
    global bot_process, bot_start_time, job_object
    try:
        if bot_process and bot_process.poll() is None:
            return False, "机器人已在运行中"
        clear_bot_logs()
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'

        if sys.platform.startswith('win'):
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            creationflags = CREATE_NEW_PROCESS_GROUP
            preexec_fn = None
        else:
            creationflags = 0
            preexec_fn = getattr(os, 'setsid', None)

        bot_process = subprocess.Popen(
            [sys.executable, 'run.py'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True, bufsize=1,
            env=env, encoding='utf-8', errors='replace',
            creationflags=creationflags if sys.platform.startswith('win') else 0,
            preexec_fn=preexec_fn
        )

        if sys.platform.startswith('win') and _WIN32_AVAILABLE and job_object:
            try:
                win32job.AssignProcessToJobObject(job_object, bot_process._handle)
                logger.info(f"已将机器人进程 (PID: {bot_process.pid}) 添加到作业对象")
            except Exception as e:
                logger.error(f"将机器人进程添加到作业对象失败: {str(e)}")

        bot_start_time = datetime.datetime.now()
        start_log_reading_thread()
        return True, "机器人启动成功"
    except Exception as e:
        logger.error(f"启动机器人失败: {str(e)}")
        return False, str(e)


def start_log_reading_thread():
    def read_output():
        try:
            while bot_process and bot_process.poll() is None:
                if bot_process.stdout:
                    line = bot_process.stdout.readline()
                    if line:
                        try:
                            line = line.strip()
                            if isinstance(line, bytes):
                                line = line.decode('utf-8', errors='replace')
                            timestamp = datetime.datetime.now().strftime('%H:%M:%S')
                            bot_logs.put(f"[{timestamp}] {line}")
                        except Exception as e:
                            logger.error(f"日志处理错误: {str(e)}")
        except Exception as e:
            logger.error(f"读取日志失败: {str(e)}")
            bot_logs.put(f"[ERROR] 读取日志失败: {str(e)}")
    threading.Thread(target=read_output, daemon=True).start()


def get_bot_uptime():
    if not bot_start_time or not bot_process or bot_process.poll() is not None:
        return "0分钟"
    delta = datetime.datetime.now() - bot_start_time
    total_seconds = int(delta.total_seconds())
    h, m, s = total_seconds // 3600, (total_seconds % 3600) // 60, total_seconds % 60
    if h > 0:
        return f"{h}小时{m}分钟{s}秒"
    elif m > 0:
        return f"{m}分钟{s}秒"
    return f"{s}秒"


@app.route('/start_bot')
def start_bot():
    success, message = start_bot_process()
    return jsonify({'status': 'success' if success else 'error', 'message': message})


@app.route('/get_bot_logs')
def get_bot_logs():
    logs = []
    while not bot_logs.empty():
        logs.append(bot_logs.get())
    return jsonify({
        'status': 'success', 'logs': logs,
        'uptime': get_bot_uptime(),
        'is_running': bot_process is not None and bot_process.poll() is None
    })


def terminate_bot_process(force=False):
    global bot_process, bot_start_time
    if not bot_process or bot_process.poll() is not None:
        return False, "机器人未在运行"
    try:
        bot_process.terminate()
        try:
            bot_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if force:
                bot_process.kill()
                bot_process.wait()

        if sys.platform.startswith('win'):
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(bot_process.pid)], capture_output=True)
        else:
            killpg = getattr(os, 'killpg', None)
            getpgid = getattr(os, 'getpgid', None)
            if killpg and getpgid:
                import signal as _signal
                killpg(getpgid(bot_process.pid), _signal.SIGTERM)
            else:
                bot_process.kill()

        bot_process = None
        bot_start_time = None
        timestamp = datetime.datetime.now().strftime('%H:%M:%S')
        bot_logs.put(f"[{timestamp}] 正在关闭监听线程...")
        bot_logs.put(f"[{timestamp}] 正在关闭系统...")
        bot_logs.put(f"[{timestamp}] 系统已退出")
        return True, "机器人已停止"
    except Exception as e:
        logger.error(f"停止机器人失败: {str(e)}")
        return False, f"停止失败: {str(e)}"


def clear_bot_logs():
    while not bot_logs.empty():
        bot_logs.get()


@app.route('/stop_bot')
def stop_bot():
    success, message = terminate_bot_process(force=True)
    return jsonify({'status': 'success' if success else 'error', 'message': message})


@app.route('/config')
def config():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    tasks = []
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
            tasks = (config_data
                     .get('categories', {})
                     .get('schedule_settings', {})
                     .get('settings', {})
                     .get('tasks', {})
                     .get('value', []))
    except Exception as e:
        logger.error(f"读取任务数据失败: {str(e)}")

    config_groups = parse_config_groups()
    return render_template(
        'config.html',
        config_groups=config_groups,
        tasks_json=json.dumps(tasks, ensure_ascii=False),
        is_local=is_local_network(),
        active_page='config'
    )


@app.route('/static/<path:filename>')
def serve_static(filename):
    static_folder = app.static_folder or os.path.join(ROOT_DIR, 'src/webui/static')
    return send_from_directory(static_folder, filename)


@app.route('/execute_command', methods=['POST'])
def execute_command():
    try:
        command = (request.json or {}).get('command', '').strip()

        if command.lower() == 'help':
            return jsonify({'status': 'success', 'output': (
                '可用命令:\nhelp - 显示帮助信息\nclear - 清空日志\nstatus - 显示系统状态\n'
                'version - 显示版本信息\nmemory - 显示内存使用情况\nstart - 启动机器人\n'
                'stop - 停止机器人\nrestart - 重启机器人\ncheck update - 检查更新\n'
                'execute update - 执行更新\n\n支持所有CMD命令')})
        elif command.lower() == 'clear':
            clear_bot_logs()
            return jsonify({'status': 'success', 'output': '', 'clear': True})
        elif command.lower() == 'status':
            if bot_process and bot_process.poll() is None:
                return jsonify({'status': 'success', 'output': f'机器人状态: 运行中\n运行时间: {get_bot_uptime()}'})
            return jsonify({'status': 'success', 'output': '机器人状态: 已停止'})
        elif command.lower() == 'version':
            return jsonify({'status': 'success', 'output': 'KouriChat v1.3.1'})
        elif command.lower() == 'memory':
            memory = psutil.virtual_memory()
            return jsonify({'status': 'success', 'output': f'内存使用: {memory.percent}% ({memory.used/1024**3:.1f}GB/{memory.total/1024**3:.1f}GB)'})
        elif command.lower() == 'start':
            success, message = start_bot_process()
            return jsonify({'status': 'success' if success else 'error', ('output' if success else 'error'): message})
        elif command.lower() == 'stop':
            success, message = terminate_bot_process(force=True)
            return jsonify({'status': 'success' if success else 'error', ('output' if success else 'error'): message})
        elif command.lower() == 'restart':
            if bot_process and bot_process.poll() is None:
                success, _ = terminate_bot_process(force=True)
                if not success:
                    return jsonify({'status': 'error', 'error': '重启失败: 无法停止当前进程'})
            time.sleep(2)
            success, message = start_bot_process()
            return jsonify({'status': 'success' if success else 'error', ('output' if success else 'error'): ('机器人已重启' if success else f'重启失败: {message}')})
        elif command.lower() == 'check update':
            try:
                updater = Updater()
                result = updater.check_for_updates()
                if result.get('has_update', False):
                    output = (f"发现新版本: {result.get('cloud_version', 'unknown')}\n"
                              f"当前版本: {result.get('local_version', 'unknown')}\n"
                              f"更新内容: {result.get('description', '无详细说明')}\n"
                              "您可以输入 'execute update' 命令开始更新")
                else:
                    output = "当前已是最新版本"
                return jsonify({'status': 'success', 'output': output})
            except Exception as e:
                return jsonify({'status': 'error', 'error': f'检查更新失败: {str(e)}'})
        elif command.lower() == 'execute update':
            return jsonify({'status': 'success', 'output': '正在启动更新进程，请查看实时更新日志...'})
        else:
            try:
                process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, encoding='utf-8', errors='replace')
                stdout, stderr = process.communicate(timeout=30)
                if stderr:
                    return jsonify({'status': 'error', 'error': stderr})
                return jsonify({'status': 'success', 'output': stdout or '命令执行成功，无输出'})
            except subprocess.TimeoutExpired:
                process.kill()
                return jsonify({'status': 'error', 'error': '命令执行超时'})
            except Exception as e:
                return jsonify({'status': 'error', 'error': f'执行命令失败: {str(e)}'})
    except Exception as e:
        return jsonify({'status': 'error', 'error': f'执行命令失败: {str(e)}'})


@app.route('/check_dependencies')
def check_dependencies():
    try:
        python_version = sys.version.split()[0]
        pip_path = shutil.which('pip')
        has_pip = pip_path is not None
        requirements_path = os.path.join(ROOT_DIR, 'requirements.txt')
        has_requirements = os.path.exists(requirements_path)

        dependencies_status = "unknown"
        missing_deps = []

        if has_requirements and has_pip:
            try:
                process = subprocess.Popen([sys.executable, '-m', 'pip', 'list'],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, _ = process.communicate()
                stdout = stdout.decode('utf-8')
                installed_packages = {
                    line.split()[0].lower()
                    for line in stdout.split('\n')[2:] if line.strip()
                }

                with open(requirements_path, 'r', encoding='utf-8') as f:
                    required_packages = set()
                    for line in f:
                        line = line.strip()
                        if (not line or line.startswith('#') or line.startswith('-i ')
                                or line.startswith('-r ') or line.startswith('--')):
                            continue
                        pkg = line.split('=')[0].split('>')[0].split('<')[0].split('~')[0].split('[')[0].strip().lower()
                        if pkg:
                            required_packages.add(pkg)

                missing_deps = [p for p in required_packages if p not in installed_packages]
                dependencies_status = "complete" if not missing_deps else "incomplete"
            except Exception as e:
                logger.error(f"检查依赖时出错: {str(e)}")
                dependencies_status = "error"
        else:
            dependencies_status = "complete" if not has_requirements else "incomplete"

        return jsonify({
            'status': 'success',
            'python_version': python_version,
            'has_pip': has_pip,
            'has_requirements': has_requirements,
            'dependencies_status': dependencies_status,
            'missing_dependencies': missing_deps
        })
    except Exception as e:
        logger.error(f"依赖检查失败: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, 'src/webui/static'),
        'mom.ico', mimetype='image/vnd.microsoft.icon'
    )


def cleanup_processes():
    global bot_process, job_object
    try:
        if bot_process:
            try:
                parent = psutil.Process(bot_process.pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except Exception:
                        try:
                            child.kill()
                        except Exception:
                            pass
                bot_process.terminate()
                try:
                    gone, alive = psutil.wait_procs(children + [parent], timeout=3)
                    for p in alive:
                        try:
                            p.kill()
                        except Exception:
                            pass
                except Exception:
                    pass
                if sys.platform.startswith('win'):
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(bot_process.pid)], capture_output=True)
                bot_process = None
            except Exception as e:
                logger.error(f"清理机器人进程失败: {str(e)}")

        current_process = psutil.Process()
        children = current_process.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except Exception:
                try:
                    child.kill()
                except Exception:
                    pass
        try:
            gone, alive = psutil.wait_procs(children, timeout=3)
            for p in alive:
                try:
                    p.kill()
                except Exception:
                    pass
        except Exception:
            pass
    except Exception as e:
        logger.error(f"清理进程失败: {str(e)}")


def signal_handler(signum, frame):
    logger.info(f"收到信号: {signum}")
    cleanup_processes()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
if sys.platform.startswith('win'):
    try:
        signal.signal(signal.SIGBREAK, signal_handler)
    except Exception:
        pass

atexit.register(cleanup_processes)


def open_browser(port):
    def _open_browser():
        time.sleep(1.5)
        webbrowser.open(f"http://localhost:{port}")
    threading.Thread(target=_open_browser, daemon=True).start()


def create_job_object():
    global job_object
    if not (sys.platform.startswith('win') and _WIN32_AVAILABLE):
        return False
    try:
        job_object = win32job.CreateJobObject(None, "KouriChatBotJob")
        info = win32job.QueryInformationJobObject(job_object, win32job.JobObjectExtendedLimitInformation)
        info['BasicLimitInformation']['LimitFlags'] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job_object, win32job.JobObjectExtendedLimitInformation, info)
        try:
            current_process = win32process.GetCurrentProcess()
            win32job.AssignProcessToJobObject(job_object, current_process)
            logger.info("已创建作业对象并将当前进程添加到作业中")
        except Exception as assign_error:
            if hasattr(assign_error, 'winerror') and assign_error.winerror == 5:
                logger.warning("无法将当前进程添加到作业对象（权限不足），但这不影响程序运行")
                return True
            raise
        return True
    except Exception as e:
        logger.error(f"创建作业对象失败: {str(e)}")
    return False


def setup_console_control_handler():
    if not (sys.platform.startswith('win') and _WIN32_AVAILABLE):
        return
    try:
        def handler(dwCtrlType):
            if dwCtrlType in (win32con.CTRL_CLOSE_EVENT, win32con.CTRL_LOGOFF_EVENT, win32con.CTRL_SHUTDOWN_EVENT):
                logger.info("检测到控制台关闭事件，正在清理进程...")
                cleanup_processes()
                return True
            return False
        win32api.SetConsoleCtrlHandler(handler, True)
        logger.info("已设置控制台关闭事件处理器")
    except Exception as e:
        logger.error(f"设置控制台关闭事件处理器失败: {str(e)}")


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def is_local_network() -> bool:
    client_ip = request.remote_addr
    if client_ip is None:
        return True
    return (client_ip == '127.0.0.1' or client_ip.startswith('192.168.')
            or client_ip.startswith('10.') or client_ip.startswith('172.16.'))


@app.before_request
def check_auth():
    public_routes = ['login', 'static', 'init_password']
    if request.endpoint in public_routes:
        return
    from data.config import config
    if not config.auth.admin_password:
        return redirect(url_for('init_password'))
    if is_local_network():
        session['logged_in'] = True
        return
    if not session.get('logged_in'):
        return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    from data.config import config
    if not config.auth.admin_password:
        return redirect(url_for('init_password'))
    if request.method == 'GET':
        if session.get('logged_in'):
            return redirect(url_for('dashboard'))
        if is_local_network():
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        return render_template('login.html')
    data = request.get_json()
    password = data.get('password')
    remember_me = data.get('remember_me', False)
    if hash_password(password) == config.auth.admin_password:
        session.clear()
        session['logged_in'] = True
        if remember_me:
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=30)
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': '密码错误'})


@app.route('/init_password', methods=['GET', 'POST'])
def init_password():
    from data.config import config
    if request.method == 'GET':
        if config.auth.admin_password:
            return redirect(url_for('login'))
        return render_template('init_password.html')
    try:
        data = request.get_json()
        if not data or 'password' not in data:
            return jsonify({'status': 'error', 'message': '无效的请求数据'})
        if config.auth.admin_password:
            return jsonify({'status': 'error', 'message': '密码已经设置'})
        hashed_password = hash_password(data['password'])
        if config.update_password(hashed_password):
            importlib.reload(sys.modules['data.config'])
            from data.config import config
            if not config.auth.admin_password:
                return jsonify({'status': 'error', 'message': '密码保存失败'})
            session.clear()
            session['logged_in'] = True
            return jsonify({'status': 'success'})
        return jsonify({'status': 'error', 'message': '保存密码失败'})
    except Exception as e:
        logger.error(f"初始化密码失败: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/get_model_configs')
def get_model_configs():
    try:
        configs = None
        models_path = os.path.join(ROOT_DIR, 'src/autoupdate/cloud/models.json')
        try:
            from src.autoupdate.updater import check_cloud_info
            cloud_info = check_cloud_info()
            if cloud_info and cloud_info.get('models'):
                configs = cloud_info['models']
        except Exception as cloud_error:
            logger.warning(f"从云端获取模型列表失败: {str(cloud_error)}")

        if configs is None:
            if not os.path.exists(models_path):
                return jsonify({'status': 'error', 'message': '模型配置文件不存在'})
            with open(models_path, 'r', encoding='utf-8') as f:
                configs = json.load(f)

        active_providers = sorted(
            [p for p in configs['api_providers'] if p.get('status') == 'active'],
            key=lambda x: x.get('priority', 999)
        )
        return_configs = {
            'api_providers': active_providers,
            'models': {
                p['id']: [m for m in configs['models'].get(p['id'], []) if m.get('status') == 'active']
                for p in active_providers
            }
        }
        return jsonify(return_configs)
    except Exception as e:
        logger.error(f"获取模型配置失败: {str(e)}")
        return jsonify({'status': 'error', 'message': f'获取模型配置失败: {str(e)}'})


@app.route('/save_quick_setup', methods=['POST'])
def save_quick_setup():
    """保存快速设置（Bot Token + API Key）"""
    try:
        new_config = request.json or {}
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                current_config = json.load(f)
        except Exception:
            current_config = {"categories": {}}

        current_config.setdefault("categories", {})

        # 保存 Bot Token → bot_settings
        if "bot_token" in new_config:
            bot_settings = current_config["categories"].setdefault("bot_settings", {
                "title": "Telegram Bot 配置",
                "settings": {}
            })
            bot_settings.setdefault("settings", {})["token"] = {
                "value": new_config["bot_token"],
                "type": "string",
                "description": "Telegram Bot Token",
                "is_secret": True
            }

        # 保存 API Key → llm_settings
        if "api_key" in new_config:
            llm = current_config["categories"].setdefault("llm_settings", {
                "title": "大语言模型配置",
                "settings": {}
            })
            settings = llm.setdefault("settings", {})
            settings["api_key"] = {
                "value": new_config["api_key"],
                "type": "string",
                "description": "API密钥",
                "is_secret": True
            }
            settings.setdefault("base_url", {"value": "https://api.kourichat.com/v1", "type": "string", "description": "API基础URL"})
            settings.setdefault("model", {"value": "kourichat-v3", "type": "string", "description": "使用的模型"})
            settings.setdefault("max_tokens", {"value": 2000, "type": "number", "description": "最大token数"})
            settings.setdefault("temperature", {"value": 1.1, "type": "number", "description": "温度参数"})
            settings.setdefault("auto_model_switch", {"value": False, "type": "boolean", "description": "自动切换模型"})

        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(current_config, f, ensure_ascii=False, indent=4)

        importlib.reload(sys.modules['data.config'])
        return jsonify({"status": "success", "message": "设置已保存"})
    except Exception as e:
        logger.error(f"保存快速设置失败: {str(e)}")
        return jsonify({"status": "error", "message": str(e)})


@app.route('/quick_setup')
def quick_setup():
    return render_template('quick_setup.html')


@app.route('/get_available_avatars')
def get_available_avatars_route():
    try:
        avatar_base_dir = os.path.join(ROOT_DIR, "data", "avatars")
        if not os.path.exists(avatar_base_dir):
            os.makedirs(avatar_base_dir)
        avatars = []
        for item in os.listdir(avatar_base_dir):
            avatar_dir = os.path.join(avatar_base_dir, item)
            if not os.path.isdir(avatar_dir):
                continue
            avatar_md_path = os.path.join(avatar_dir, "avatar.md")
            emojis_dir = os.path.join(avatar_dir, "emojis")
            if not os.path.exists(avatar_md_path):
                continue
            if not os.path.exists(emojis_dir):
                try:
                    os.makedirs(emojis_dir)
                except Exception:
                    continue
            avatars.append(f"data/avatars/{item}")
        return jsonify({'status': 'success', 'avatars': avatars})
    except Exception as e:
        logger.error(f"获取人设列表失败: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/load_avatar_content')
def load_avatar_content():
    try:
        avatar_name = request.args.get('avatar', 'MONO')
        avatar_path = os.path.join(ROOT_DIR, 'data', 'avatars', avatar_name, 'avatar.md')
        os.makedirs(os.path.dirname(avatar_path), exist_ok=True)
        if not os.path.exists(avatar_path):
            with open(avatar_path, 'w', encoding='utf-8') as f:
                f.write("# Task\n请在此输入任务描述\n\n# Role\n请在此输入角色设定\n\n# Appearance\n请在此输入外表描述\n\n")
        sections = {}
        current_section = None
        content = ""
        with open(avatar_path, 'r', encoding='utf-8') as file:
            for line in file:
                if line.startswith('# '):
                    if current_section:
                        sections[current_section.lower()] = content.strip()
                    current_section = line[2:].strip()
                    content = ""
                else:
                    content += line
            if current_section:
                sections[current_section.lower()] = content.strip()
        with open(avatar_path, 'r', encoding='utf-8') as file:
            raw_content = file.read()
        return jsonify({'status': 'success', 'content': sections, 'raw_content': raw_content})
    except Exception as e:
        logger.error(f"加载人设内容失败: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/get_tasks', methods=['GET'])
def get_tasks():
    try:
        config_data = load_config_file()
        tasks = (config_data
                 .get('categories', {})
                 .get('schedule_settings', {})
                 .get('settings', {})
                 .get('tasks', {})
                 .get('value', []))
        return jsonify({'status': 'success', 'tasks': tasks})
    except Exception as e:
        logger.error(f"获取任务失败: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/save_task', methods=['POST'])
def save_task():
    try:
        task_data = request.json
        required_fields = ['task_id', 'chat_id', 'content', 'schedule_type', 'schedule_time']
        for field in required_fields:
            if field not in task_data:
                return jsonify({'status': 'error', 'message': f'缺少必要字段: {field}'})

        config_data = load_config_file()
        cats = config_data.setdefault('categories', {})
        ss = cats.setdefault('schedule_settings', {'title': '定时任务配置', 'settings': {}})
        tasks_cfg = ss.setdefault('settings', {}).setdefault('tasks', {'value': [], 'type': 'array', 'description': '定时任务列表'})
        tasks = tasks_cfg['value']

        task_index = next((i for i, t in enumerate(tasks) if t.get('task_id') == task_data['task_id']), None)
        if task_index is not None:
            tasks[task_index] = task_data
        else:
            tasks.append(task_data)

        if not save_config_file(config_data):
            return jsonify({'status': 'error', 'message': '保存配置文件失败'}), 500

        reinitialize_tasks()
        return jsonify({'status': 'success', 'message': '任务已保存'})
    except Exception as e:
        logger.error(f"保存任务失败: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/delete_task', methods=['POST'])
def delete_task():
    try:
        task_id = (request.json or {}).get('task_id')
        if not task_id:
            return jsonify({'status': 'error', 'message': '未提供任务ID'})

        config_data = load_config_file()
        tasks_cfg = (config_data
                     .get('categories', {})
                     .get('schedule_settings', {})
                     .get('settings', {})
                     .get('tasks'))
        if tasks_cfg:
            tasks_cfg['value'] = [t for t in tasks_cfg['value'] if t.get('task_id') != task_id]
            if not save_config_file(config_data):
                return jsonify({'status': 'error', 'message': '保存配置文件失败'}), 500
            reinitialize_tasks()
            return jsonify({'status': 'success', 'message': '任务已删除'})

        return jsonify({'status': 'error', 'message': '找不到任务配置'})
    except Exception as e:
        logger.error(f"删除任务失败: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/get_all_configs')
def get_all_configs():
    """获取所有最新的配置数据"""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        configs = {}
        tasks = []
        cats = config_data.get('categories', {})

        # Bot 配置（新增）
        if 'bot_settings' in cats and 'settings' in cats['bot_settings']:
            bot = cats['bot_settings']['settings']
            configs['Bot 配置'] = {}
            if 'token' in bot:
                configs['Bot 配置']['BOT_TOKEN'] = bot['token']
            if 'name' in bot:
                configs['Bot 配置']['BOT_NAME'] = bot['name']
            if 'proxy_url' in bot:
                configs['Bot 配置']['PROXY_URL'] = bot['proxy_url']

        # 用户设置（Chat ID 列表 + 群聊配置）
        if 'user_settings' in cats and 'settings' in cats['user_settings']:
            us = cats['user_settings']['settings']
            configs.setdefault('Bot 配置', {})
            if 'telegram_chat_ids' in us:
                configs['Bot 配置']['TELEGRAM_CHAT_IDS'] = us['telegram_chat_ids']
            if 'group_chat_config' in us:
                configs['Bot 配置']['GROUP_CHAT_CONFIG'] = us['group_chat_config']

        # LLM
        if 'llm_settings' in cats and 'settings' in cats['llm_settings']:
            llm = cats['llm_settings']['settings']
            configs['基础配置'] = {}
            for ui_key, cfg_key in [('DEEPSEEK_API_KEY', 'api_key'), ('DEEPSEEK_BASE_URL', 'base_url'),
                                     ('MODEL', 'model'), ('MAX_TOKEN', 'max_tokens'),
                                     ('TEMPERATURE', 'temperature'), ('AUTO_MODEL_SWITCH', 'auto_model_switch')]:
                if cfg_key in llm:
                    configs['基础配置'][ui_key] = llm[cfg_key]

        # 媒体
        if 'media_settings' in cats and 'settings' in cats['media_settings']:
            media = cats['media_settings']['settings']
            configs['图像识别API配置'] = {}
            if 'image_recognition' in media:
                ir = media['image_recognition']
                for ui_key, cfg_key in [('VISION_API_KEY', 'api_key'), ('VISION_BASE_URL', 'base_url'),
                                         ('VISION_TEMPERATURE', 'temperature'), ('VISION_MODEL', 'model')]:
                    if cfg_key in ir:
                        configs['图像识别API配置'][ui_key] = ir[cfg_key]
            configs['TTS 服务配置'] = {}
            if 'text_to_speech' in media:
                tts = media['text_to_speech']
                for ui_key, cfg_key in [('TTS_API_KEY', 'tts_api_key'), ('TTS_MODEL_ID', 'tts_model_id')]:
                    if cfg_key in tts:
                        configs['TTS 服务配置'][ui_key] = {'value': tts[cfg_key].get('value', '')}

        # 行为
        if 'behavior_settings' in cats and 'settings' in cats['behavior_settings']:
            beh = cats['behavior_settings']['settings']
            configs['主动消息配置'] = {}
            if 'auto_message' in beh:
                am = beh['auto_message']
                if 'content' in am:
                    configs['主动消息配置']['AUTO_MESSAGE'] = am['content']
                if 'countdown' in am:
                    for k in ('min_hours', 'max_hours'):
                        if k in am['countdown']:
                            configs['主动消息配置'][('MIN' if 'min' in k else 'MAX') + '_COUNTDOWN_HOURS'] = am['countdown'][k]
            if 'quiet_time' in beh:
                for k in ('start', 'end'):
                    if k in beh['quiet_time']:
                        configs['主动消息配置']['QUIET_TIME_' + k.upper()] = beh['quiet_time'][k]
            configs['消息配置'] = {}
            if 'message_queue' in beh and 'timeout' in beh['message_queue']:
                configs['消息配置']['QUEUE_TIMEOUT'] = beh['message_queue']['timeout']
            configs['人设配置'] = {}
            if 'context' in beh:
                for ui_key, cfg_key in [('MAX_GROUPS', 'max_groups'), ('AVATAR_DIR', 'avatar_dir')]:
                    if cfg_key in beh['context']:
                        configs['人设配置'][ui_key] = beh['context'][cfg_key]

        # 网络搜索
        if 'network_search_settings' in cats and 'settings' in cats['network_search_settings']:
            ns = cats['network_search_settings']['settings']
            configs['网络搜索配置'] = {}
            for ui_key, cfg_key in [('NETWORK_SEARCH_ENABLED', 'search_enabled'),
                                     ('WEBLENS_ENABLED', 'weblens_enabled'),
                                     ('NETWORK_SEARCH_API_KEY', 'api_key'),
                                     ('NETWORK_SEARCH_BASE_URL', 'base_url')]:
                if cfg_key in ns:
                    configs['网络搜索配置'][ui_key] = ns[cfg_key]

        # 意图识别
        if 'intent_recognition_settings' in cats and 'settings' in cats['intent_recognition_settings']:
            ir = cats['intent_recognition_settings']['settings']
            configs['意图识别配置'] = {}
            for ui_key, cfg_key in [('INTENT_API_KEY', 'api_key'), ('INTENT_BASE_URL', 'base_url'),
                                     ('INTENT_MODEL', 'model'), ('INTENT_TEMPERATURE', 'temperature')]:
                if cfg_key in ir:
                    configs['意图识别配置'][ui_key] = ir[cfg_key]

        # 定时任务
        tasks = (cats.get('schedule_settings', {})
                     .get('settings', {})
                     .get('tasks', {})
                     .get('value', []))

        return jsonify({'status': 'success', 'configs': configs, 'tasks': tasks})
    except Exception as e:
        logger.error(f"获取所有配置数据失败: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/get_announcement')
def get_announcement():
    try:
        from src.autoupdate.announcement import get_current_announcement
        announcement = get_current_announcement()
        if announcement and announcement.get('enabled', False):
            return jsonify(announcement)
        return jsonify({'enabled': True, 'title': '欢迎使用KouriChat', 'content': '欢迎使用KouriChat！如有问题请联系开发者。'})
    except Exception as e:
        logger.error(f"获取公告失败: {e}")
        return jsonify({'enabled': False, 'title': '公告获取失败', 'content': f'<div class="text-danger">错误信息: {str(e)}</div>'})


@app.route('/dismiss_announcement', methods=['POST'])
def dismiss_announcement():
    try:
        from src.autoupdate.announcement import dismiss_announcement as dismiss_func
        data = request.get_json() if request.is_json else {}
        announcement_id = data.get('announcement_id', None)
        success = dismiss_func(announcement_id)
        if success:
            return jsonify({'success': True, 'message': '公告已设置为不再显示'})
        return jsonify({'success': False, 'message': '忽略公告失败'}), 400
    except Exception as e:
        logger.error(f"忽略公告失败: {e}")
        return jsonify({'success': False, 'message': f'操作失败: {str(e)}'}), 500


@app.route('/get_vision_api_configs')
def get_vision_api_configs():
    try:
        vision_providers = [
            {"id": "kourichat-global", "name": "KouriChat API (推荐)", "url": "https://api.kourichat.com/v1",
             "register_url": "https://api.kourichat.com/register", "status": "active", "priority": 1},
            {"id": "moonshot", "name": "Moonshot（月之暗面）", "url": "https://api.moonshot.cn/v1",
             "register_url": "https://platform.moonshot.cn/console/api-keys", "status": "active", "priority": 2},
            {"id": "openai", "name": "OpenAI", "url": "https://api.openai.com/v1",
             "register_url": "https://platform.openai.com/api-keys", "status": "active", "priority": 3},
        ]
        vision_models = {
            "kourichat-global": [
                {"id": "kourichat-vision", "name": "kourichat-vision"},
                {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
                {"id": "gpt-4o", "name": "GPT-4o"}
            ],
            "moonshot": [{"id": "moonshot-v1-8k-vision-preview", "name": "moonshot-v1-8k-vision-preview"}]
        }
        return jsonify({"status": "success", "api_providers": vision_providers, "models": vision_models})
    except Exception as e:
        logger.error(f"获取图像识别API配置失败: {str(e)}")
        return jsonify({"status": "error", "message": str(e)})


def main():
    from data.config import config

    if sys.platform.startswith('win'):
        os.system("@chcp 65001 >nul")

    print("\n" + "=" * 50)
    print_status("配置管理系统启动中...", "info", "LAUNCH")
    print("-" * 50)

    create_job_object()
    setup_console_control_handler()

    print_status("检查系统目录...", "info", "FILE")
    for d in [templates_dir, os.path.join(static_dir, 'js'), os.path.join(static_dir, 'css')]:
        os.makedirs(d, exist_ok=True)

    print_status("检查配置文件...", "info", "CONFIG")
    if not os.path.exists(config.config_path):
        print_status("错误：配置文件不存在！", "error", "CROSS")
        return
    print_status("配置文件检查完成", "success", "CHECK")

    try:
        cli = sys.modules['flask.cli']
        if hasattr(cli, 'show_server_banner'):
            setattr(cli, 'show_server_banner', lambda *x: None)
    except (KeyError, AttributeError):
        pass

    host = '0.0.0.0'
    port = 8502

    def is_port_available(p):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('localhost', p))
                return True
        except OSError:
            return False

    original_port = port
    while not is_port_available(port):
        port += 1
        if port > 9000:
            print_status(f"无法找到可用端口（尝试了{original_port}-{port}）", "error", "CROSS")
            return

    if port != original_port:
        print_status(f"端口{original_port}被占用，自动选择端口{port}", "warning", "WARNING")

    print_status("正在启动Web服务...", "info", "INTERNET")
    print("-" * 50)
    print_status("配置管理系统已就绪！", "success", "STAR_1")
    print_status("可通过以下地址访问:", "info", "CHAIN")
    print(f"  Local:   http://localhost:{port}")
    print(f"  Local:   http://127.0.0.1:{port}")

    try:
        addresses = socket.getaddrinfo(socket.gethostname(), None)
        for addr in addresses:
            ip = addr[4][0]
            if isinstance(ip, str) and '.' in ip and ip != '127.0.0.1':
                print(f"  Network: http://{ip}:{port}")
    except Exception:
        pass

    print("=" * 50 + "\n")
    open_browser(port)

    try:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except PermissionError as e:
        print_status(f"权限错误：{str(e)}", "error", "CROSS")
        print_status("请尝试以管理员身份运行程序", "warning", "WARNING")
    except OSError as e:
        if "access" in str(e).lower() or "permission" in str(e).lower():
            print_status(f"端口访问被拒绝：{str(e)}", "error", "CROSS")
        else:
            print_status(f"网络错误：{str(e)}", "error", "CROSS")
    except Exception as e:
        print_status(f"启动Web服务失败：{str(e)}", "error", "CROSS")


@app.route('/install_dependencies', methods=['POST'])
def install_dependencies():
    try:
        requirements_path = os.path.join(ROOT_DIR, 'requirements.txt')
        if not os.path.exists(requirements_path):
            return jsonify({'status': 'error', 'message': '找不到requirements.txt文件'})
        process = subprocess.Popen([sys.executable, '-m', 'pip', 'install', '-r', requirements_path],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        stdout = stdout.decode('utf-8')
        stderr = stderr.decode('utf-8')
        output = stdout if stdout else stderr
        has_error = process.returncode != 0 and not any(
            msg in (stdout + stderr).lower() for msg in ['already satisfied', 'successfully installed']
        )
        return jsonify({'status': 'error' if has_error else 'success', 'output': output,
                        **({'message': '安装依赖失败'} if has_error else {})})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n")
        print_status("正在关闭服务...", "warning", "STOP")
        cleanup_processes()
        print_status("配置管理系统已停止", "info", "BYE")
        print("\n")
    except Exception as e:
        print_status(f"系统错误: {str(e)}", "error", "ERROR")
        cleanup_processes()
