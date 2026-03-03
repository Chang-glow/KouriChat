import logging
import threading
import time
import os
import shutil
import asyncio
import re
import queue
import collections

from src.utils.console import print_status

# 率先初始化网络适配器以覆盖所有网络库
try:
    from src.autoupdate.core.manager import initialize_system
    initialize_system()
    print_status("网络适配器初始化成功", "success", "CHECK")
except Exception as e:
    print_status(f"网络适配器初始化失败: {str(e)}", "error", "CROSS")

# 导入其余模块
from data.config import config, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL, MAX_TOKEN, TEMPERATURE, MAX_GROUPS
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder,
    Application,
    CommandHandler,
    MessageHandler as TGMessageHandler,
    ContextTypes,
    filters,
)
from src.handlers.emoji import EmojiHandler
from src.handlers.image import ImageHandler
from src.handlers.message import MessageHandler as BotMessageHandler
from src.services.ai.image_recognition_service import ImageRecognitionService
from modules.memory.memory_service import MemoryService
from modules.memory.content_generator import ContentGenerator
from src.utils.logger import LoggerConfig
from colorama import init
from src.AutoTasker.autoTasker import AutoTasker
from src.handlers.autosend import AutoSendHandler

# 创建一个事件对象来控制线程的终止
stop_event = threading.Event()

# 获取项目根目录
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 检查并初始化配置文件
config_path = os.path.join(root_dir, 'src', 'config', 'config.json')
config_template_path = os.path.join(root_dir, 'src', 'config', 'config.json.template')

if not os.path.exists(config_path) and os.path.exists(config_template_path):
    _tmp_logger = logging.getLogger('main')
    _tmp_logger.info("配置文件不存在，正在从模板创建...")
    shutil.copy2(config_template_path, config_path)
    _tmp_logger.info(f"已从模板创建配置文件: {config_path}")

# 初始化colorama
init()

# 全局变量
logger = None


def initialize_logging():
    """初始化日志系统"""
    global logger

    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logger_config = LoggerConfig(root_dir)
    logger = logger_config.setup_logger('main')

    logging.getLogger("autoupdate").setLevel(logging.DEBUG)
    logging.getLogger("autoupdate.core").setLevel(logging.DEBUG)
    logging.getLogger("autoupdate.interceptor").setLevel(logging.DEBUG)
    logging.getLogger("autoupdate.network_optimizer").setLevel(logging.DEBUG)


# 消息队列
private_message_queue = queue.Queue()
group_message_queue = queue.Queue()


# ── 修复4：有界消息去重缓存，防止 processed_messages 无限增长 ────────────────────

_PROCESSED_MSG_MAX = 2000


class BoundedMessageCache:
    """线程安全的有界消息去重缓存，超出上限时自动淘汰最旧条目"""

    def __init__(self, maxsize: int = _PROCESSED_MSG_MAX):
        self._maxsize = maxsize
        self._keys: set = set()
        self._order: collections.deque = collections.deque()
        self._lock = threading.Lock()

    def __contains__(self, key: str) -> bool:
        return key in self._keys

    def add(self, key: str):
        with self._lock:
            if key in self._keys:
                return
            self._keys.add(key)
            self._order.append(key)
            while len(self._order) > self._maxsize:
                oldest = self._order.popleft()
                self._keys.discard(oldest)


# ── 消息封装 ──────────────────────────────────────────────────────────────────────

class TelegramMessage:
    """统一封装 Telegram Update，供处理器使用"""

    def __init__(self, update: Update):
        msg = update.effective_message
        self.id = str(msg.message_id)
        self.type = "friend"
        self.content = msg.text or msg.caption or ""
        self.sender = str(update.effective_user.id) if update.effective_user else "unknown"
        self.sender_name = update.effective_user.full_name if update.effective_user else "unknown"
        self.chat_id = str(update.effective_chat.id)
        self.chat_type = update.effective_chat.type  # "private" / "group" / "supergroup"
        self.photo = msg.photo[-1] if msg.photo else None
        self.document = msg.document if msg.document else None
        self.raw = msg


# ── 私聊机器人 ────────────────────────────────────────────────────────────────────

class PrivateChatBot:
    """处理 Telegram 私聊消息"""

    def __init__(self, msg_handler, image_recognition_service, auto_sender,
                 emoji_handler, bot: Bot, tg_loop, memory_service_ref):  # 修复1：新增 memory_service_ref
        self.message_handler = msg_handler
        self.image_recognition_service = image_recognition_service
        self.auto_sender = auto_sender
        self.emoji_handler = emoji_handler
        self.bot = bot
        self.tg_loop = tg_loop
        self.memory_service = memory_service_ref          # 修复1：保存引用
        self._initialized_users: set = set()              # 修复1：已初始化用户缓存
        self.robot_name = getattr(getattr(config, 'bot', None), 'name', 'Bot')

        default_avatar_path = config.behavior.context.avatar_dir
        self.current_avatar = os.path.basename(default_avatar_path)
        logger.info(f"私聊机器人初始化完成 - 名称: {self.robot_name}, 人设: {self.current_avatar}")

    def _ensure_user_memory(self, username: str):
        """修复1 & 5：首次遇到该用户时动态初始化记忆文件"""
        if username in self._initialized_users:
            return
        try:
            avatar_dir_path = os.path.join(root_dir, config.behavior.context.avatar_dir)
            avatar_name = os.path.basename(avatar_dir_path)
            self.memory_service.initialize_memory_files(avatar_name, user_id=username)
            self._initialized_users.add(username)
            logger.info(f"[私聊] 用户 '{username}' 记忆初始化完成")
        except Exception as e:
            logger.error(f"[私聊] 用户 '{username}' 记忆初始化失败: {str(e)}")

    def handle_private_message(self, tg_msg: TelegramMessage):
        try:
            username = tg_msg.sender
            sender_name = tg_msg.sender_name
            content = tg_msg.content

            self._ensure_user_memory(username)   # 修复1：首次调用时初始化
            self.auto_sender.start_countdown()
            logger.info(f"[私聊] 来自: {sender_name}({username})")
            logger.debug(f"[私聊] 内容: {content}")

            img_path = None
            is_image_recognition = False

            if tg_msg.photo or self._is_image_doc(tg_msg):
                img_path = self._download_image_sync(tg_msg)

            if img_path:
                recognized_text = self.image_recognition_service.recognize_image(img_path, False)
                content = recognized_text if not content else f"{content} {recognized_text}"
                is_image_recognition = True

            if content:
                self.message_handler.handle_user_message(
                    content=content,
                    chat_id=tg_msg.chat_id,
                    sender_name=sender_name,
                    username=username,
                    is_group=False,
                    is_image_recognition=is_image_recognition
                )
        except Exception as e:
            logger.error(f"[私聊] 消息处理失败: {str(e)}")

    def _is_image_doc(self, tg_msg):
        return (tg_msg.document and tg_msg.document.mime_type and
                tg_msg.document.mime_type.startswith("image/"))

    def _download_image_sync(self, tg_msg):
        async def _dl():
            file_id = tg_msg.photo.file_id if tg_msg.photo else tg_msg.document.file_id
            f = await self.bot.get_file(file_id)
            save_dir = os.path.join(root_dir, "temp_images")
            os.makedirs(save_dir, exist_ok=True)
            path = os.path.join(save_dir, f"{f.file_id}.jpg")
            await f.download_to_drive(path)
            return path
        return asyncio.run_coroutine_threadsafe(_dl(), self.tg_loop).result(timeout=30)


# ── 群聊机器人 ────────────────────────────────────────────────────────────────────

class GroupChatBot:
    """处理 Telegram 群聊消息"""

    def __init__(self, message_handler_class, base_config, auto_sender, emoji_handler,
                 image_recognition_service, bot: Bot, tg_loop):
        self.message_handlers = {}
        self.message_handler_class = message_handler_class
        self.base_config = base_config
        self.auto_sender = auto_sender
        self.emoji_handler = emoji_handler
        self.image_recognition_service = image_recognition_service
        self.bot = bot
        self.tg_loop = tg_loop
        self.robot_name = getattr(getattr(config, 'bot', None), 'name', 'Bot')
        logger.info(f"群聊机器人初始化完成 - 名称: {self.robot_name}")

    def get_group_handler(self, group_id: str, group_config=None):
        if group_id not in self.message_handlers:
            avatar_path = (group_config.avatar if group_config and group_config.avatar
                           else self.base_config.behavior.context.avatar_dir)
            full_avatar_path = os.path.join(root_dir, avatar_path)
            prompt_path = os.path.join(full_avatar_path, "avatar.md")

            if os.path.exists(prompt_path):
                with open(prompt_path, "r", encoding="utf-8") as f:
                    group_prompt_content = f.read()
            else:
                logger.error(f"群聊人设文件不存在: {prompt_path}")
                group_prompt_content = prompt_content

            handler = self.message_handler_class(
                root_dir=root_dir,
                api_key=self.base_config.llm.api_key,
                base_url=self.base_config.llm.base_url,
                model=self.base_config.llm.model,
                max_token=self.base_config.llm.max_tokens,
                temperature=self.base_config.llm.temperature,
                max_groups=self.base_config.behavior.context.max_groups,
                robot_name=self.robot_name,
                prompt_content=group_prompt_content,
                image_handler=image_handler,
                emoji_handler=self.emoji_handler,
                memory_service=memory_service,
                content_generator=content_generator,
                bot=self.bot,
                tg_loop=self.tg_loop
            )
            handler.current_avatar = os.path.basename(full_avatar_path)
            handler.avatar_real_names = handler._extract_avatar_names(full_avatar_path)
            self.message_handlers[group_id] = handler
            logger.info(f"[群聊] 为群 '{group_id}' 创建专用处理器，人设: {handler.current_avatar}")

        return self.message_handlers[group_id]

    def handle_group_message(self, tg_msg: TelegramMessage, group_config=None):
        try:
            username = tg_msg.sender
            sender_name = tg_msg.sender_name
            group_id = tg_msg.chat_id
            content = tg_msg.content

            logger.info(f"[群聊] 群: {group_id}, 发送者: {sender_name}({username})")
            logger.debug(f"[群聊] 内容: {content}")

            handler = self.get_group_handler(group_id, group_config)

            img_path = None
            is_image_recognition = False

            if content and self.robot_name:
                content = re.sub(rf'@{re.escape(self.robot_name)}\s*', '', content).strip()

            if tg_msg.photo or self._is_image_doc(tg_msg):
                img_path = self._download_image_sync(tg_msg)

            if img_path:
                recognized_text = self.image_recognition_service.recognize_image(img_path, False)
                content = recognized_text if not content else f"{content} {recognized_text}"
                is_image_recognition = True

            if content:
                handler.handle_user_message(
                    content=content,
                    chat_id=group_id,
                    sender_name=sender_name,
                    username=username,
                    is_group=True,
                    is_image_recognition=is_image_recognition
                )
        except Exception as e:
            logger.error(f"[群聊] 消息处理失败: {str(e)}")

    def _is_image_doc(self, tg_msg):
        return (tg_msg.document and tg_msg.document.mime_type and
                tg_msg.document.mime_type.startswith("image/"))

    def _download_image_sync(self, tg_msg):
        async def _dl():
            file_id = tg_msg.photo.file_id if tg_msg.photo else tg_msg.document.file_id
            f = await self.bot.get_file(file_id)
            save_dir = os.path.join(root_dir, "temp_images")
            os.makedirs(save_dir, exist_ok=True)
            path = os.path.join(save_dir, f"{f.file_id}.jpg")
            await f.download_to_drive(path)
            return path
        return asyncio.run_coroutine_threadsafe(_dl(), self.tg_loop).result(timeout=30)


# ── 消息处理线程 ──────────────────────────────────────────────────────────────────

def private_message_processor():
    logger.info("私聊消息处理线程启动")
    while not stop_event.is_set():
        try:
            msg_data = private_message_queue.get(timeout=1)
            if msg_data is None:
                break
            private_chat_bot.handle_private_message(msg_data)
            private_message_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            logger.error(f"私聊消息处理线程出错: {str(e)}")


def group_message_processor():
    logger.info("群聊消息处理线程启动")
    while not stop_event.is_set():
        try:
            msg_data = group_message_queue.get(timeout=1)
            if msg_data is None:
                break
            tg_msg, group_config = msg_data
            group_chat_bot.handle_group_message(tg_msg, group_config)
            group_message_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            logger.error(f"群聊消息处理线程出错: {str(e)}")


# ── 全局服务变量 ──────────────────────────────────────────────────────────────────

prompt_content = ""
emoji_handler = None
image_handler = None
memory_service = None
content_generator = None
message_handler = None
image_recognition_service = None
auto_sender = None
private_chat_bot = None
group_chat_bot = None
ROBOT_TG_NAME = ""
processed_messages = BoundedMessageCache(maxsize=_PROCESSED_MSG_MAX)  # 修复4


def initialize_services(bot: Bot, tg_loop):
    """初始化所有服务实例"""
    global prompt_content, emoji_handler, image_handler, memory_service, content_generator
    global message_handler, image_recognition_service, auto_sender
    global private_chat_bot, group_chat_bot, ROBOT_TG_NAME

    # 检查热更新模块
    try:
        from src.autoupdate.core.manager import get_manager
        status = get_manager().get_status()
        print_status("热更新模块已就绪" if status else "热更新模块状态异常",
                     "success" if status else "warning",
                     "CHECK" if status else "CROSS")
    except Exception as e:
        print_status(f"检查热更新模块状态时出现异常: {e}", "error", "ERROR")

    # 读取人设文件
    avatar_dir_path = os.path.join(root_dir, config.behavior.context.avatar_dir)
    prompt_path = os.path.join(avatar_dir_path, "avatar.md")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            prompt_content = f.read()
    else:
        raise FileNotFoundError(f"avatar.md 文件不存在: {prompt_path}")

    # 创建各服务实例
    emoji_handler = EmojiHandler(root_dir)
    image_handler = ImageHandler(
        root_dir=root_dir,
        api_key=config.llm.api_key,
        base_url=config.llm.base_url,
        image_model=config.media.image_generation.model
    )
    memory_service = MemoryService(
        root_dir=root_dir,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=MODEL,
        max_token=MAX_TOKEN,
        temperature=TEMPERATURE,
        max_groups=MAX_GROUPS
    )
    content_generator = ContentGenerator(
        root_dir=root_dir,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=MODEL,
        max_token=MAX_TOKEN,
        temperature=TEMPERATURE
    )
    image_recognition_service = ImageRecognitionService(
        api_key=config.media.image_recognition.api_key,
        base_url=config.media.image_recognition.base_url,
        temperature=config.media.image_recognition.temperature,
        model=config.media.image_recognition.model
    )

    # 获取 Bot 自身 username
    async def _get_bot_name():
        me = await bot.get_me()
        return me.username or me.first_name

    try:
        ROBOT_TG_NAME = asyncio.run_coroutine_threadsafe(
            _get_bot_name(), tg_loop
        ).result(timeout=10)
        logger.info(f"获取到 Bot 名称: {ROBOT_TG_NAME}")
    except Exception as e:
        logger.warning(f"获取 Bot 名称失败: {str(e)}")
        ROBOT_TG_NAME = ""

    # 创建消息处理器
    message_handler = BotMessageHandler(
        root_dir=root_dir,
        api_key=config.llm.api_key,
        base_url=config.llm.base_url,
        model=config.llm.model,
        max_token=config.llm.max_tokens,
        temperature=config.llm.temperature,
        max_groups=config.behavior.context.max_groups,
        robot_name=ROBOT_TG_NAME,
        prompt_content=prompt_content,
        image_handler=image_handler,
        emoji_handler=emoji_handler,
        memory_service=memory_service,
        content_generator=content_generator,
        bot=bot,
        tg_loop=tg_loop
    )

    # 修复2：取第一个群聊 chat_id 作为主动消息默认目标
    default_chat_id = None
    if hasattr(config, 'user') and config.user.group_chat_config:
        first_gc = config.user.group_chat_config[0]
        if first_gc.group_name:
            default_chat_id = str(first_gc.group_name)

    auto_sender = AutoSendHandler(
        message_handler,
        config,
        default_chat_id=default_chat_id   # 修复2：传入默认发送目标
    )

    private_chat_bot = PrivateChatBot(
        message_handler, image_recognition_service, auto_sender,
        emoji_handler, bot, tg_loop,
        memory_service_ref=memory_service  # 修复1：传入 memory_service
    )
    group_chat_bot = GroupChatBot(
        BotMessageHandler, config, auto_sender, emoji_handler,
        image_recognition_service, bot, tg_loop
    )

    auto_sender.start_countdown()


# ── PTB 异步 Handler ──────────────────────────────────────────────────────────────

async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot 已就绪，请开始对话。")


async def on_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """私聊消息回调：无白名单限制，所有私聊均响应"""
    tg_msg = TelegramMessage(update)
    msg_key = f"{tg_msg.chat_id}_{tg_msg.id}"
    if msg_key in processed_messages:
        return
    processed_messages.add(msg_key)
    logger.debug(f"[分发] 私聊 -> 私聊队列: {tg_msg.chat_id}")
    private_message_queue.put(tg_msg)


async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """群聊消息回调：根据触发条件决定是否响应"""
    tg_msg = TelegramMessage(update)
    chat_id = tg_msg.chat_id
    content = tg_msg.content

    msg_key = f"{chat_id}_{tg_msg.id}"
    if msg_key in processed_messages:
        return
    processed_messages.add(msg_key)

    should_respond = False
    trigger_reason = ""
    group_config = None

    # 1. 检查群聊配置触发词
    if config and hasattr(config, 'user') and config.user.group_chat_config:
        for gc_config in config.user.group_chat_config:
            raw = str(gc_config.group_name).strip()
            # 修复3：跳过非数字 ID 的配置项并给出警告
            if not raw.lstrip('-').isdigit():
                logger.warning(
                    f"群聊配置 group_name='{raw}' 不是有效的数字 ID，已跳过。"
                    "请填写 Telegram 群聊的数字 ID（通常以 -100 开头）。"
                )
                continue
            if raw == chat_id:
                group_config = gc_config
                for trigger in gc_config.triggers:
                    if trigger and trigger in content:
                        trigger_reason = f"群聊触发词({trigger})"
                        should_respond = True
                        break
                break

    # 2. 检查 @Bot mention
    if not should_respond:
        at_enabled = group_config.enable_at_trigger if group_config is not None else True
        if at_enabled and ROBOT_TG_NAME and f"@{ROBOT_TG_NAME}" in content:
            trigger_reason = f"被@了Bot({ROBOT_TG_NAME})"
            should_respond = True

    # 3. 检查人设名字
    if not should_respond and group_config:
        temp_handler = group_chat_bot.get_group_handler(chat_id, group_config)
        if hasattr(temp_handler, 'avatar_real_names'):
            for name in temp_handler.avatar_real_names:
                if name and name in content:
                    trigger_reason = f"提到了群聊人设名字({name})"
                    should_respond = True
                    break

    if should_respond:
        logger.debug(f"[分发] 群聊触发响应 - 原因: {trigger_reason} -> 群聊队列: {chat_id}")
        group_message_queue.put((tg_msg, group_config))
    else:
        logger.debug(f"群聊消息未触发响应 - 群: {chat_id}, 内容: {content}")


# ── 自动任务 ──────────────────────────────────────────────────────────────────────

def initialize_auto_tasks(msg_handler):
    print_status("初始化自动任务系统...", "info", "CLOCK")
    try:
        auto_tasker = AutoTasker(msg_handler)
        print_status("创建 AutoTasker 实例成功", "success", "CHECK")
        auto_tasker.scheduler.remove_all_jobs()

        if hasattr(config, 'behavior') and hasattr(config.behavior, 'schedule_settings'):
            schedule_settings = config.behavior.schedule_settings
            if schedule_settings and schedule_settings.tasks:
                tasks = schedule_settings.tasks
                print_status(f"从配置文件读取到 {len(tasks)} 个任务", "info", "TASK")
                tasks_added = 0
                for task in tasks:
                    try:
                        auto_tasker.add_task(
                            task_id=task.task_id,
                            chat_id=task.chat_id,
                            content=task.content,
                            schedule_type=task.schedule_type,
                            schedule_time=task.schedule_time
                        )
                        tasks_added += 1
                        print_status(f"成功添加任务 {task.task_id}: {task.content}", "success", "CHECK")
                    except Exception as e:
                        print_status(f"添加任务 {task.task_id} 失败: {str(e)}", "error", "ERROR")
                print_status(f"成功添加 {tasks_added}/{len(tasks)} 个任务", "info", "TASK")
            else:
                print_status("配置文件中没有找到任务", "warning", "WARNING")
        else:
            print_status("未找到任务配置信息", "warning", "WARNING")

        return auto_tasker
    except Exception as e:
        print_status(f"初始化自动任务系统失败: {str(e)}", "error", "ERROR")
        if logger:
            logger.error(f"初始化自动任务系统失败: {str(e)}")
        return None


def switch_avatar(new_avatar_name):
    global emoji_handler, private_chat_bot, group_chat_bot

    config.behavior.context.avatar_dir = f"avatars/{new_avatar_name}"
    emoji_handler = EmojiHandler(root_dir)

    if private_chat_bot:
        private_chat_bot.emoji_handler = emoji_handler
        private_chat_bot.message_handler.emoji_handler = emoji_handler

    if group_chat_bot:
        group_chat_bot.emoji_handler = emoji_handler
        for gh in group_chat_bot.message_handlers.values():
            gh.emoji_handler = emoji_handler


# ── 主函数 ────────────────────────────────────────────────────────────────────────

def main():
    private_thread = None
    group_thread = None

    try:
        initialize_logging()

        tg_token = (getattr(getattr(config, 'bot', None), 'token', None) or
                    os.environ.get("TELEGRAM_BOT_TOKEN", ""))
        if not tg_token:
            print_status(
                "未配置 Telegram Bot Token！"
                "请在配置文件 config.bot.token 或环境变量 TELEGRAM_BOT_TOKEN 中设置",
                "error", "CROSS"
            )
            return

        # 获取代理配置
        proxy_url = getattr(getattr(config, 'bot', None), 'proxy_url', None)
        if proxy_url:
            application = ApplicationBuilder().token(tg_token).proxy_url(proxy_url).build()
            logger.info(f"使用代理: {proxy_url}")
        else:
            application = ApplicationBuilder().token(tg_token).build()

        # 注册 Handler
        application.add_handler(CommandHandler("start", on_start))
        application.add_handler(
            TGMessageHandler(
                filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO | filters.Document.IMAGE),
                on_private_message
            )
        )
        application.add_handler(
            TGMessageHandler(
                (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP) &
                (filters.TEXT | filters.PHOTO | filters.Document.IMAGE),
                on_group_message
            )
        )

        # post_init：在 PTB 内部 event loop 就绪后执行初始化
        async def post_init(app: Application):
            nonlocal private_thread, group_thread

            tg_loop = asyncio.get_event_loop()
            print_status("初始化服务实例...", "info", "BOT")
            initialize_services(app.bot, tg_loop)
            print_status("服务实例初始化完成", "success", "CHECK")

            # 验证记忆目录
            avatar_dir_path = os.path.join(root_dir, config.behavior.context.avatar_dir)
            avatar_name = os.path.basename(avatar_dir_path)
            os.makedirs(os.path.join(avatar_dir_path, "memory"), exist_ok=True)

            # 修复5：仅初始化已配置群聊的记忆（私聊用户改为动态初始化，见 PrivateChatBot._ensure_user_memory）
            print_status("初始化群聊记忆文件...", "info", "FILE")
            configured_chat_ids = set()
            if hasattr(config, 'user') and config.user.group_chat_config:
                for gc in config.user.group_chat_config:
                    raw = str(gc.group_name).strip()
                    if raw.lstrip('-').isdigit():   # 修复3：与 on_group_message 保持一致，跳过非数字
                        configured_chat_ids.add(raw)
            for cid in configured_chat_ids:
                memory_service.initialize_memory_files(avatar_name, user_id=cid)
                print_status(f"群聊 '{cid}' 记忆初始化完成", "success", "CHECK")

            # 确保人设文件存在
            prompt_path = os.path.join(avatar_dir_path, "avatar.md")
            if not os.path.exists(prompt_path):
                with open(prompt_path, "w", encoding="utf-8") as f:
                    f.write("# 核心人格\n[默认内容]")
                print_status("创建人设提示文件", "warning", "WARNING")

            # 启动并行消息处理线程
            print_status("启动并行消息处理系统...", "info", "ANTENNA")
            private_thread = threading.Thread(
                target=private_message_processor, name="PrivateProcessor", daemon=True
            )
            group_thread = threading.Thread(
                target=group_message_processor, name="GroupProcessor", daemon=True
            )
            private_thread.start()
            group_thread.start()
            print_status("并行消息处理系统已启动", "success", "CHECK")
            print_status("  ├─ 私聊处理器线程", "info", "USER")
            print_status("  └─ 群聊处理器线程", "info", "USERS")

            # 初始化自动任务
            auto_tasker = initialize_auto_tasks(message_handler)
            if not auto_tasker:
                print_status("自动任务系统初始化失败", "error", "ERROR")

            print("-" * 50)
            print_status("系统初始化完成，开始监听 Telegram 消息...", "success", "STAR_2")
            print("=" * 50)

        application.post_init = post_init

        # 启动 Bot（阻塞直到收到停止信号）
        application.run_polling(allowed_updates=Update.ALL_TYPES)

    except Exception as e:
        print_status(f"主程序异常: {str(e)}", "error", "ERROR")
        if logger:
            logger.error(f"主程序异常: {str(e)}", exc_info=True)
    finally:
        if auto_sender is not None:
            try:
                auto_sender.stop()
            except Exception:
                pass

        stop_event.set()

        try:
            private_message_queue.put(None)
            group_message_queue.put(None)
        except Exception:
            pass

        for thread_name, thread in [("私聊处理器", private_thread), ("群聊处理器", group_thread)]:
            if thread and thread.is_alive():
                print_status(f"正在关闭{thread_name}线程...", "info", "SYNC")
                thread.join(timeout=3)
                if thread.is_alive():
                    print_status(f"{thread_name}线程未能正常关闭", "warning", "WARNING")

        print_status("正在关闭系统...", "warning", "STOP")
        print_status("系统已退出", "info", "BYE")
        print("\n")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n")
        print_status("用户终止程序", "warning", "STOP")
        print_status("感谢使用，再见！", "info", "BYE")
        print("\n")
    except Exception as e:
        print_status(f"程序异常退出: {str(e)}", "error", "ERROR")