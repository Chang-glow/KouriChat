"""
消息处理模块
负责处理聊天消息，包括:
- 消息队列管理
- 消息分发处理
- API响应处理
- 多媒体消息处理
"""
import logging
import threading
import time
import re
import asyncio
from datetime import datetime
from src.services.database import Session, ChatMessage
import random
import os
import json
from src.services.ai.llm_service import LLMService
from src.services.ai.network_search_service import NetworkSearchService
from data.config import config, WEBLENS_ENABLED, NETWORK_SEARCH_ENABLED
from modules.recognition import ReminderRecognitionService, SearchRecognitionService
from .debug import DebugCommandHandler

# 导入emoji库用于处理表情符号
import emoji

# 修改logger获取方式，确保与main模块一致
logger = logging.getLogger('main')


class MessageHandler:
    def __init__(self, root_dir, api_key, base_url, model, max_token, temperature,
                 max_groups, robot_name, prompt_content, image_handler, emoji_handler, memory_service,
                 content_generator=None, bot=None, tg_loop=None):
        """
        Args:
            ...（原有参数不变）
            bot: telegram.Bot 实例，用于发送消息
            tg_loop: Bot 所在的 asyncio event loop，用于跨线程调用异步方法
        """
        self.root_dir = root_dir
        self.api_key = api_key
        self.model = model
        self.max_token = max_token
        self.temperature = temperature
        self.max_groups = max_groups
        self.robot_name = robot_name
        self.prompt_content = prompt_content

        # Telegram Bot 相关（替代原有的 WeChat 实例）
        self.bot = bot          # telegram.Bot 实例
        self.tg_loop = tg_loop  # Bot 所在的 asyncio event loop

        # 使用 LLMService 替换直接的 OpenAI 客户端
        self.deepseek = LLMService(
            api_key=api_key,
            base_url=base_url,
            model=model,
            max_token=max_token,
            temperature=temperature,
            max_groups=max_groups,
            auto_model_switch=getattr(config.llm, 'auto_model_switch', False)
        )

        # 消息队列相关
        self.message_queues = {}
        self.queue_timers = {}
        self.QUEUE_TIMEOUT = config.behavior.message_queue.timeout
        self.queue_lock = threading.Lock()
        self.chat_contexts = {}

        # 添加 handlers
        self.image_handler = image_handler
        self.emoji_handler = emoji_handler
        self.memory_service = memory_service

        # 当前角色名
        avatar_path = os.path.join(self.root_dir, config.behavior.context.avatar_dir)
        self.current_avatar = os.path.basename(avatar_path)

        # 从人设文件中提取真实名字
        self.avatar_real_names = self._extract_avatar_names(avatar_path)
        logger.info(f"当前使用角色: {self.current_avatar}, 识别名字: {self.avatar_real_names}")

        # 使用传入的内容生成器实例，或创建新实例
        self.content_generator = content_generator
        if self.content_generator is None:
            try:
                from modules.memory.content_generator import ContentGenerator
                self.content_generator = ContentGenerator(
                    root_dir=root_dir,
                    api_key=config.llm.api_key,
                    base_url=config.llm.base_url,
                    model=config.llm.model,
                    max_token=config.llm.max_tokens,
                    temperature=config.llm.temperature
                )
                logger.info("已创建内容生成器实例")
            except Exception as e:
                logger.error(f"创建内容生成器实例失败: {str(e)}")
                self.content_generator = None

        # 初始化调试命令处理器
        self.debug_handler = DebugCommandHandler(
            root_dir=root_dir,
            memory_service=memory_service,
            llm_service=self.deepseek,
            content_generator=self.content_generator
        )

        # 需要保留原始格式的命令列表
        self.preserve_format_commands = [None, '/diary', '/state', '/letter', '/list', '/pyq', '/gift', '/shopping']
        logger.info("调试命令处理器已初始化")

        # 初始化识别服务
        self.remind_request_recognitor = ReminderRecognitionService(self.deepseek)
        self.search_request_recognitor = SearchRecognitionService(self.deepseek)
        logger.info("意图识别服务已初始化")

        # 初始化提醒服务
        from modules.reminder import ReminderService
        self.reminder_service = ReminderService(self, self.memory_service)
        logger.info("提醒服务已初始化")

        # 初始化网络搜索服务
        self.network_search_service = NetworkSearchService(self.deepseek)
        logger.info("网络搜索服务已初始化")

    # ── Telegram 发送工具 ────────────────────────────────────────────────────────

    def _run_async(self, coro):
        """在 Bot 的 event loop 中跨线程执行协程，阻塞直到完成。"""
        if self.bot is None or self.tg_loop is None:
            logger.warning("Bot 未初始化，无法发送 Telegram 消息")
            return None
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self.tg_loop)
            return future.result(timeout=30)
        except Exception as e:
            logger.error(f"Telegram 发送失败: {str(e)}")
            return None

    def _tg_send_text(self, chat_id: str, text: str):
        """向 Telegram 发送纯文本消息。"""
        if not text or not text.strip():
            return
        self._run_async(
            self.bot.send_message(chat_id=int(chat_id), text=text.strip())
        )

    def _tg_send_file(self, chat_id: str, filepath: str):
        """向 Telegram 发送文件/图片。"""
        if not filepath or not os.path.exists(filepath):
            logger.warning(f"文件不存在，跳过发送: {filepath}")
            return
        ext = os.path.splitext(filepath)[1].lower()
        image_exts = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
        if ext in image_exts:
            with open(filepath, 'rb') as f:
                self._run_async(
                    self.bot.send_photo(chat_id=int(chat_id), photo=f)
                )
        else:
            with open(filepath, 'rb') as f:
                self._run_async(
                    self.bot.send_document(chat_id=int(chat_id), document=f)
                )

    # ── 人设管理 ─────────────────────────────────────────────────────────────────

    def switch_avatar_temporarily(self, avatar_path: str):
        """临时切换人设（不修改全局配置，仅用于群聊）"""
        try:
            full_avatar_path = os.path.join(self.root_dir, avatar_path)
            prompt_path = os.path.join(full_avatar_path, "avatar.md")
            if os.path.exists(prompt_path):
                with open(prompt_path, "r", encoding="utf-8") as file:
                    self.prompt_content = file.read()
                self.current_avatar = os.path.basename(full_avatar_path)
                self.avatar_real_names = self._extract_avatar_names(full_avatar_path)
                logger.info(f"临时切换人设到: {self.current_avatar}, 识别名字: {self.avatar_real_names}")
            else:
                logger.error(f"人设文件不存在: {prompt_path}")
        except Exception as e:
            logger.error(f"临时切换人设失败: {str(e)}")

    def restore_default_avatar(self):
        """恢复到默认人设"""
        try:
            default_avatar_path = config.behavior.context.avatar_dir
            full_avatar_path = os.path.join(self.root_dir, default_avatar_path)
            prompt_path = os.path.join(full_avatar_path, "avatar.md")
            if os.path.exists(prompt_path):
                with open(prompt_path, "r", encoding="utf-8") as file:
                    self.prompt_content = file.read()
                self.current_avatar = os.path.basename(full_avatar_path)
                self.avatar_real_names = self._extract_avatar_names(full_avatar_path)
                logger.info(f"恢复到默认人设: {self.current_avatar}, 识别名字: {self.avatar_real_names}")
            else:
                logger.error(f"默认人设文件不存在: {prompt_path}")
        except Exception as e:
            logger.error(f"恢复默认人设失败: {str(e)}")

    def switch_avatar(self, avatar_path: str):
        """切换人设"""
        try:
            config.behavior.context.avatar_dir = avatar_path
            full_avatar_path = os.path.join(self.root_dir, avatar_path)
            prompt_path = os.path.join(full_avatar_path, "avatar.md")
            if os.path.exists(prompt_path):
                with open(prompt_path, "r", encoding="utf-8") as file:
                    self.prompt_content = file.read()
                self.current_avatar = os.path.basename(full_avatar_path)
                self.avatar_real_names = self._extract_avatar_names(full_avatar_path)
                logger.info(f"成功切换人设到: {self.current_avatar}, 识别名字: {self.avatar_real_names}")
            else:
                logger.error(f"人设文件不存在: {prompt_path}")
        except Exception as e:
            logger.error(f"切换人设失败: {str(e)}")

    def _extract_avatar_names(self, avatar_path: str) -> list:
        """从人设文件中提取可能的名字"""
        names = []
        try:
            avatar_file = os.path.join(avatar_path, "avatar.md")
            if os.path.exists(avatar_file):
                with open(avatar_file, 'r', encoding='utf-8') as f:
                    content = f.read()

                # 提取"你是xxx"模式的名字
                matches = re.findall(r'你是([^，,。！!？?\\s]+)', content)
                for match in matches:
                    if match not in names and len(match) <= 6 and '机器' not in match:
                        names.append(match)

                # 提取"名字[：:]\\s*xxx"模式的名字
                matches = re.findall(r'名字[：:]\s*([^，,。！!？?\\s\\n]+)', content)
                for match in matches:
                    if match not in names and len(match) <= 6:
                        names.append(match)

                # 提取"扮演xxx"模式的名字
                matches = re.findall(r'扮演([^，,。！!？?\\s]+)', content)
                for match in matches:
                    if match not in names and len(match) <= 6 and any('\u4e00' <= c <= '\u9fff' for c in match):
                        names.append(match)

        except Exception as e:
            logger.warning(f"提取人设名字失败: {str(e)}")

        if not names:
            names = [self.current_avatar]
        return names

    # ── 队列与消息处理辅助 ────────────────────────────────────────────────────────

    def _get_queue_key(self, chat_id: str, sender_name: str, is_group: bool) -> str:
        """生成队列键值"""
        return f"{chat_id}_{sender_name}" if is_group else chat_id

    def _add_at_tag_if_needed(self, reply: str, sender_name: str, is_group: bool) -> str:
        """群聊中在回复前添加 @发送者（Telegram 使用 mention 格式）

        Telegram 的 mention 格式为 @username（username 不含空格）。
        由于 Telegram 用户名可能含有 _ 等字符，这里直接使用 sender_name
        作为展示名；若需精确 mention 请改为使用 user_id 构造 tg-user-id 链接。
        """
        if not is_group:
            return reply

        at_prefix = f"@{sender_name}"
        # 检查是否已包含 @标签，避免重复
        if reply.startswith(at_prefix):
            logger.debug("AI回复中已包含@标签，无需添加")
            return reply
        elif reply.startswith("@") and sender_name in reply.split()[0]:
            logger.debug("AI回复中已包含@标签，无需添加")
            return reply
        else:
            logger.debug("群聊环境下添加@标签")
            return f"{at_prefix} {reply}"

    def _get_user_relationship_info(self, sender_name: str) -> str:
        """获取用户关系信息，用于群聊环境判断"""
        try:
            avatar_name = self.current_avatar
            has_private_memory = self.memory_service.has_user_memory(avatar_name, sender_name)
            special_relationship = self._get_special_relationship(avatar_name, sender_name)

            if has_private_memory:
                base_info = f"发送者 {sender_name} 与你有私聊记忆。"
            else:
                base_info = f"发送者 {sender_name} 没有私聊记忆。"

            if special_relationship:
                return f"## 当前发送者关系状态：\n{base_info} 特殊关系：{special_relationship}。"
            return f"## 当前发送者关系状态：\n{base_info}"

        except Exception as e:
            logger.error(f"获取用户关系信息失败: {str(e)}")
            return f"## 当前发送者关系状态：\n发送者 {sender_name} 关系状态未知，请保持礼貌友好的态度。"

    def _get_special_relationship(self, avatar_name: str, user_name: str) -> str:
        """从核心记忆中查找特殊关系设定"""
        try:
            avatars_dir = os.path.join(self.root_dir, "data", "avatars", avatar_name, "memory")
            if not os.path.exists(avatars_dir):
                return ""

            for user_dir in os.listdir(avatars_dir):
                core_memory_path = os.path.join(avatars_dir, user_dir, "core_memory.json")
                if os.path.exists(core_memory_path):
                    try:
                        with open(core_memory_path, "r", encoding="utf-8") as f:
                            core_memory = json.load(f)
                            content = core_memory.get("content", "")
                            if user_name in content:
                                relationship_keywords = {
                                    "朋友": f"{user_name}是朋友",
                                    "敌人": f"{user_name}是敌人",
                                    "兄弟": f"{user_name}是兄弟",
                                    "姐妹": f"{user_name}是姐妹",
                                    "同事": f"{user_name}是同事",
                                    "老师": f"{user_name}是老师",
                                    "学生": f"{user_name}是学生"
                                }
                                for keyword, description in relationship_keywords.items():
                                    if keyword in content and user_name in content:
                                        return description
                    except Exception as e:
                        logger.debug(f"读取核心记忆文件失败: {str(e)}")
                        continue
            return ""
        except Exception as e:
            logger.error(f"查找特殊关系失败: {str(e)}")
            return ""

    def save_message(self, sender_id: str, sender_name: str, message: str, reply: str,
                     is_system_message: bool = False):
        """保存聊天记录到数据库和短期记忆"""
        try:
            # 清理回复中的@前缀，防止幻觉
            clean_reply = reply
            at_prefix = f"@{sender_name} "
            if reply.startswith(at_prefix):
                clean_reply = reply[len(at_prefix):]

            session = Session()
            chat_message = ChatMessage(
                sender_id=sender_id,
                sender_name=sender_name,
                message=message,
                reply=reply
            )
            session.add(chat_message)
            session.commit()
            session.close()

            avatar_name = self.current_avatar
            self.memory_service.add_conversation(avatar_name, message, clean_reply, sender_id, is_system_message)

        except Exception as e:
            logger.error(f"保存消息失败: {str(e)}")

    # ── API 响应 ─────────────────────────────────────────────────────────────────

    def get_api_response(self, message: str, user_id: str, is_group: bool = False) -> str:
        """获取 API 回复"""
        avatar_name = self.current_avatar
        try:
            avatar_content = self.prompt_content
            logger.debug(f"角色提示文件大小: {len(avatar_content)} bytes")

            core_memory = self.memory_service.get_core_memory(avatar_name, user_id=user_id)
            core_memory_prompt = f"# 核心记忆\n{core_memory}" if core_memory else ""
            logger.debug(f"核心记忆长度: {len(core_memory)}")

            recent_context = None
            if user_id not in self.deepseek.chat_contexts:
                recent_context = self.memory_service.get_recent_context(avatar_name, user_id)
                if recent_context:
                    logger.info(f"程序启动：为用户 {user_id} 加载 {len(recent_context)} 条历史上下文消息")

            if is_group:
                group_prompt_path = os.path.join(self.root_dir, "src", "base", "group.md")
                with open(group_prompt_path, "r", encoding="utf-8") as f:
                    group_chat_prompt = f.read().strip()
                relationship_info = self._get_user_relationship_info(user_id)
                combined_system_prompt = f"{group_chat_prompt}\n\n{relationship_info}\n\n{avatar_content}"
            else:
                combined_system_prompt = avatar_content

            if hasattr(self, 'system_prompts') and user_id in self.system_prompts and self.system_prompts[user_id]:
                additional_prompt = "\n\n".join(self.system_prompts[user_id])
                logger.info(f"使用系统提示词: {additional_prompt[:100]}...")
                combined_system_prompt = f"{combined_system_prompt}\n\n参考信息:\n{additional_prompt}"
                self.system_prompts[user_id] = []

            response = self.deepseek.get_response(
                message=message,
                user_id=user_id,
                system_prompt=combined_system_prompt,
                previous_context=recent_context,
                core_memory=core_memory_prompt
            )
            return response

        except Exception as e:
            logger.error(f"获取API响应失败: {str(e)}")
            return self.deepseek.get_response(message, user_id, self.prompt_content)

    # ── 消息入口 ─────────────────────────────────────────────────────────────────

    def handle_user_message(self, content: str, chat_id: str, sender_name: str,
                            username: str, is_group: bool = False, is_image_recognition: bool = False):
        """统一的消息处理入口"""
        try:
            logger.info(f"收到消息 - 来自: {sender_name}" + (" (群聊)" if is_group else ""))
            logger.debug(f"消息内容: {content}")

            # 处理调试命令
            if self.debug_handler.is_debug_command(content):
                logger.info(f"检测到调试命令: {content}")

                def command_callback(command, reply, chat_id):
                    try:
                        reply = self._add_at_tag_if_needed(reply, sender_name, is_group)
                        self._send_command_response(command, reply, chat_id)
                        logger.info(f"异步处理命令完成: {command}")
                    except Exception as e:
                        logger.error(f"异步处理命令失败: {str(e)}")

                intercept, response = self.debug_handler.process_command(
                    command=content,
                    current_avatar=self.current_avatar,
                    user_id=chat_id,
                    chat_id=chat_id,
                    callback=command_callback
                )

                if intercept:
                    if response:
                        response = self._add_at_tag_if_needed(response, sender_name, is_group)
                        self._send_raw_message(response, chat_id)
                    logger.info(f"已处理调试命令: {content}")
                    return None

            self._add_to_message_queue(content, chat_id, sender_name, username, is_group, is_image_recognition)

        except Exception as e:
            logger.error(f"处理消息失败: {str(e)}", exc_info=True)
            return None

    # ── 消息队列 ─────────────────────────────────────────────────────────────────

    def _add_to_message_queue(self, content: str, chat_id: str, sender_name: str,
                              username: str, is_group: bool, is_image_recognition: bool):
        """添加消息到队列并设置定时器"""
        has_link = False
        urls = []
        if WEBLENS_ENABLED:
            urls = self.network_search_service.detect_urls(content)
            if urls:
                has_link = True
                logger.info(f"[消息队列] 检测到链接: {urls[0]}，将在队列处理时提取内容")

        with self.queue_lock:
            queue_key = self._get_queue_key(chat_id, sender_name, is_group)

            if queue_key not in self.message_queues:
                logger.info(f"[消息队列] 创建新队列 - 用户: {sender_name}" + (" (群聊)" if is_group else ""))
                self.message_queues[queue_key] = {
                    'messages': [content],
                    'chat_id': chat_id,
                    'sender_name': sender_name,
                    'username': username,
                    'is_group': is_group,
                    'is_image_recognition': is_image_recognition,
                    'last_update': time.time(),
                    'has_link': has_link,
                    'urls': urls if has_link else []
                }
            else:
                self.message_queues[queue_key]['messages'].append(content)
                self.message_queues[queue_key]['last_update'] = time.time()
                self.message_queues[queue_key]['has_link'] = (
                    has_link | self.message_queues[queue_key]['has_link']
                )
                if has_link:
                    self.message_queues[queue_key]['urls'].append(urls[0])
                msg_count = len(self.message_queues[queue_key]['messages'])
                logger.info(f"[消息队列] 追加消息 - 用户: {sender_name}, 当前消息数: {msg_count}")

            # 重置定时器
            if queue_key in self.queue_timers and self.queue_timers[queue_key]:
                try:
                    self.queue_timers[queue_key].cancel()
                except Exception as e:
                    logger.error(f"[消息队列] 取消定时器失败: {str(e)}")
                self.queue_timers[queue_key] = None

            timer = threading.Timer(self.QUEUE_TIMEOUT, self._process_message_queue, args=[queue_key])
            timer.daemon = True
            timer.start()
            self.queue_timers[queue_key] = timer
            logger.info(f"[消息队列] 设置新定时器 - 用户: {sender_name}, {self.QUEUE_TIMEOUT}秒后处理")

    def _process_message_queue(self, queue_key: str):
        """处理消息队列"""
        try:
            with self.queue_lock:
                if queue_key not in self.message_queues:
                    return

                current_time = time.time()
                queue_data = self.message_queues[queue_key]
                last_update = queue_data['last_update']
                sender_name = queue_data['sender_name']

                if current_time - last_update < self.QUEUE_TIMEOUT - 0.1:
                    logger.info(
                        f"[消息队列] 等待更多消息 - 用户: {sender_name}, "
                        f"剩余时间: {self.QUEUE_TIMEOUT - (current_time - last_update):.1f}秒"
                    )
                    return

                queue_data = self.message_queues.pop(queue_key)
                if queue_key in self.queue_timers:
                    self.queue_timers.pop(queue_key)

                messages = queue_data['messages']
                chat_id = queue_data['chat_id']
                username = queue_data['username']
                sender_name = queue_data['sender_name']
                is_group = queue_data['is_group']
                is_image_recognition = queue_data['is_image_recognition']

                combined_message = "；".join(messages)

                logger.info(f"[消息队列] 开始处理 - 用户: {sender_name}, 消息数: {len(messages)}")
                logger.info("----------------------------------------")
                logger.info("收到消息:")
                logger.info(combined_message)
                logger.info("----------------------------------------")

                # 处理链接内容
                processed_message = combined_message
                if queue_data.get('has_link', False) and WEBLENS_ENABLED:
                    urls = queue_data.get('urls', [])
                    if urls:
                        web_results = self.network_search_service.extract_web_content(urls[0])
                        if web_results and web_results['original']:
                            processed_message = f"{combined_message}\n\n{web_results['original']}"
                            logger.info("已获取URL内容并添加至本次Prompt中")

                # 检查时间提醒和联网搜索需求
                search_handled = self._check_time_reminder_and_search(processed_message, sender_name)
                if search_handled:
                    return self._handle_text_message(processed_message, chat_id, sender_name, username, is_group)

                # 联网搜索
                if NETWORK_SEARCH_ENABLED:
                    search_intent = self.search_request_recognitor.recognize(message=combined_message)
                    if search_intent['search_required']:
                        logger.info(f"检测到搜索需求: {search_intent['search_query']}")
                        search_results = self.network_search_service.search_internet(
                            query=search_intent['search_query']
                        )
                        if search_results and search_results['original']:
                            processed_message = f"{combined_message}\n\n{search_results['original']}"

                # 识别提醒意图
                if sender_name not in ('System', 'system'):
                    tasks = self.remind_request_recognitor.recognize(combined_message)
                    if tasks != "NOT_TIME_RELATED":
                        logger.info("检测到提醒需求，正在添加至提醒列表...")
                        voice_reminder_keywords = ["电话", "语音"]
                        reminder_type = "voice" if any(k in combined_message for k in voice_reminder_keywords) else "text"
                        for task in tasks:
                            self.reminder_service.add_reminder(
                                chat_id=chat_id,
                                target_time=datetime.strptime(task["target_time"], "%Y-%m-%d %H:%M:%S"),
                                content=task["reminder_content"],
                                sender_name=sender_name,
                                reminder_type=reminder_type
                            )

                return self._handle_text_message(processed_message, chat_id, sender_name, username, is_group)

        except Exception as e:
            logger.error(f"处理消息队列失败: {e}")
            return None

    # ── 文本处理工具 ──────────────────────────────────────────────────────────────

    def _process_text_for_display(self, text: str) -> str:
        """处理文本以确保表情符号正确显示"""
        try:
            return emoji.emojize(emoji.demojize(text))
        except Exception:
            return text

    def _filter_user_tags(self, text: str) -> str:
        """过滤消息中的用户标签"""
        text = re.sub(r'<用户\s+[^>]+>\s*', '', text)
        text = re.sub(r'\s*</用户>', '', text)
        return text.strip()

    # ── 消息发送（替代原有 wx.SendMsg / wx.SendFiles）────────────────────────────

    def _send_message_with_dollar(self, reply: str, chat_id: str):
        """以 $ 为分隔符分批发送回复（适配 Telegram）"""
        # 过滤用户标签 & 处理 emoji
        reply = self._filter_user_tags(reply)
        reply = self._process_text_for_display(reply)

        if '$' in reply or '＄' in reply:
            parts = [p.strip() for p in reply.replace("＄", "$").split("$") if p.strip()]
            for part in parts:
                emotion_tags = self.emoji_handler.extract_emotion_tags(part)
                clean_part = part
                for tag in emotion_tags:
                    clean_part = clean_part.replace(f'[{tag}]', '')

                if clean_part.strip():
                    self._tg_send_text(chat_id, clean_part.strip())
                    logger.debug(f"发送消息: {clean_part[:20]}...")

                # 发送表情图片
                for emotion_type in emotion_tags:
                    try:
                        emoji_path = self.emoji_handler.get_emoji_for_emotion(emotion_type)
                        if emoji_path:
                            self._tg_send_file(chat_id, emoji_path)
                            logger.debug(f"已发送表情: {emotion_type}")
                            time.sleep(random.randint(1, 3))
                    except Exception as e:
                        logger.error(f"发送表情失败 - {emotion_type}: {str(e)}")

                time.sleep(random.randint(2, 5))
        else:
            emotion_tags = self.emoji_handler.extract_emotion_tags(reply)
            clean_reply = reply
            for tag in emotion_tags:
                clean_reply = clean_reply.replace(f'[{tag}]', '')

            if clean_reply.strip():
                self._tg_send_text(chat_id, clean_reply.strip())
                logger.debug(f"发送消息: {clean_reply[:20]}...")

            for emotion_type in emotion_tags:
                try:
                    emoji_path = self.emoji_handler.get_emoji_for_emotion(emotion_type)
                    if emoji_path:
                        self._tg_send_file(chat_id, emoji_path)
                        logger.debug(f"已发送表情: {emotion_type}")
                        time.sleep(random.randint(1, 3))
                except Exception as e:
                    logger.error(f"发送表情失败 - {emotion_type}: {str(e)}")

    def _send_raw_message(self, text: str, chat_id: str):
        """直接发送原始文本消息，保留格式（适配 Telegram）"""
        try:
            text = self._filter_user_tags(text)
            text = self._process_text_for_display(text)

            # 提取并移除表情标签
            emotion_tags = self.emoji_handler.extract_emotion_tags(text)
            clean_text = text
            for tag in emotion_tags:
                clean_text = clean_text.replace(f'[{tag}]', '')

            if clean_text:
                # 移除分隔符，保留换行格式
                clean_text = clean_text.replace('$', '').replace('＄', '')
                # Telegram 原生支持 \n 换行，无需额外转换
                self._tg_send_text(chat_id, clean_text)

        except Exception as e:
            logger.error(f"发送原始格式消息失败: {str(e)}")

    def _send_command_response(self, command: str, reply: str, chat_id: str):
        """发送命令响应，根据命令类型决定是否保留原始格式"""
        if not reply:
            return
        if command in self.preserve_format_commands:
            logger.info(f"使用原始格式发送命令响应: {command}")
            self._send_raw_message(reply, chat_id)
        else:
            self._send_message_with_dollar(reply, chat_id)

    # ── 核心文本消息处理 ──────────────────────────────────────────────────────────

    def _handle_text_message(self, content: str, chat_id: str, sender_name: str,
                             username: str, is_group: bool):
        """处理普通文本消息并通过 Telegram 发送回复"""
        command = None
        if content.startswith('/'):
            command = content.split(' ')[0].lower()
            logger.debug(f"检测到命令: {command}")

        api_content = f"[群聊消息] {sender_name}: {content}" if is_group else content

        reply = self.get_api_response(api_content, chat_id, is_group)
        logger.info(f"AI回复: {reply}")

        # 处理思考过程
        if "</think>" in reply:
            think_content, reply = reply.split("</think>", 1)
            logger.debug(f"思考过程: {think_content.strip()}")

        # 群聊添加 @ 标签
        reply = self._add_at_tag_if_needed(reply, sender_name, is_group)

        is_system_message = sender_name in ("System", "system") or username in ("System", "system")

        # 发送消息
        if command and command in self.preserve_format_commands:
            self._send_command_response(command, reply, chat_id)
        else:
            self._send_message_with_dollar(reply, chat_id)

        # 异步保存记录
        save_content = api_content if is_group else content
        threading.Thread(
            target=self.save_message,
            args=(chat_id, sender_name, save_content, reply, is_system_message),
            daemon=True
        ).start()
        if is_system_message:
            threading.Thread(
                target=self.save_message,
                args=(chat_id, chat_id, "……", reply, False),
                daemon=True
            ).start()
        return reply

    # ── 系统提示词管理 ────────────────────────────────────────────────────────────

    def _add_to_system_prompt(self, chat_id: str, content: str) -> None:
        """将内容添加到系统提示词中"""
        try:
            if not hasattr(self, 'system_prompts'):
                self.system_prompts = {}
            if chat_id not in self.system_prompts:
                self.system_prompts[chat_id] = []
            self.system_prompts[chat_id].append(content)
            if len(self.system_prompts[chat_id]) > 5:
                self.system_prompts[chat_id] = self.system_prompts[chat_id][-5:]
            logger.info(f"已将内容添加到聊天 {chat_id} 的系统提示词中")
        except Exception as e:
            logger.error(f"添加内容到系统提示词失败: {str(e)}")

    def _remove_search_content_from_context(self, chat_id: str, content: str) -> None:
        """从上下文中删除搜索内容"""
        try:
            logger.info(f"尝试从内存中删除搜索内容: {content[:50]}...")
        except Exception as e:
            logger.error(f"从上下文中删除搜索内容失败: {str(e)}")

    def _async_generate_summary(self, chat_id: str, url: str, content: str, model: str = None) -> None:
        """异步生成总结并添加到系统提示词中"""
        try:
            logger.info(f"开始等待总结生成时间: {url}")
            time.sleep(30)
            logger.info(f"开始异步生成总结: {url}")

            summary_model = model if model else config.llm.model
            summary_messages = [
                {
                    "role": "user",
                    "content": f"请将以下内容总结为简洁的要点，以便在系统提示词中使用：\n\n{content}\n\n原始链接或查询: {url}"
                }
            ]
            summary_result = self.network_search_service.llm_service.chat(
                messages=summary_messages,
                model=summary_model
            )
            if summary_result:
                if "http" in url:
                    final_summary = f"关于链接 {url} 的信息：{summary_result}"
                else:
                    final_summary = f"关于\"{url}\"的信息：{summary_result}"
                self._remove_search_content_from_context(chat_id, content)
                self._add_to_system_prompt(chat_id, final_summary)
                logger.info(f"已将异步生成的总结添加到系统提示词中: {url}")
            else:
                logger.warning(f"异步生成总结失败: {url}")
        except Exception as e:
            logger.error(f"异步生成总结失败: {str(e)}")

    def _check_time_reminder_and_search(self, content: str, sender_name: str) -> bool:
        """检查和处理时间提醒和联网搜索需求"""
        if sender_name in ("System", "system"):
            return False
        try:
            if "可作为你的回复参考" in content:
                logger.info("已联网获取过信息，直接获取回复")
                return True
        except Exception as e:
            logger.error(f"处理时间提醒和搜索失败: {str(e)}")
        return False

    # ── 兼容旧接口 ────────────────────────────────────────────────────────────────

    def add_to_queue(self, chat_id: str, content: str, sender_name: str,
                     username: str, is_group: bool = False):
        """添加消息到队列（兼容旧接口）"""
        return self._add_to_message_queue(content, chat_id, sender_name, username, is_group, False)

    def process_messages(self, chat_id: str):
        """处理消息队列中的消息（已废弃，保留兼容）"""
        logger.warning("process_messages方法已废弃，使用handle_message代替")
