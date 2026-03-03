import logging
import threading
import time
import os
import sys
from datetime import datetime
from typing import Dict, List

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
from modules.reminder.call import Call
from modules.tts.service import tts
from modules.memory import MemoryService
from src.handlers.message import MessageHandler
from src.services.ai.llm_service import LLMService
from data.config import config

logger = logging.getLogger('main')


class ReminderTask:
    """单个提醒任务结构"""
    def __init__(self, task_id: str, chat_id: str, target_time: datetime,
                 content: str, sender_name: str, reminder_type: str = "text"):
        self.task_id = task_id
        self.chat_id = chat_id
        self.target_time = target_time
        self.content = content
        self.sender_name = sender_name
        self.reminder_type = reminder_type
        self.audio_path = None

    def is_due(self) -> bool:
        return datetime.now() >= self.target_time


class ReminderService:
    def __init__(self, message_handler: MessageHandler, mem_service: MemoryService):
        self.message_handler = message_handler
        self.mem_service = mem_service
        self.llm_service = message_handler.deepseek

        # 从 message_handler 获取 Telegram Bot 相关引用（替代原有 self.wx）
        self.bot = getattr(message_handler, 'bot', None)
        self.tg_loop = getattr(message_handler, 'tg_loop', None)

        self.active_reminders: Dict[str, ReminderTask] = {}
        self._lock = threading.Lock()
        self._start_polling_thread()
        logger.info("统一提醒服务已启动")

    def _start_polling_thread(self):
        thread = threading.Thread(target=self._poll_reminders_loop, daemon=True)
        thread.start()

    def _poll_reminders_loop(self):
        while True:
            due_tasks: List[ReminderTask] = []
            with self._lock:
                for _, task in list(self.active_reminders.items()):
                    if task.is_due():
                        due_tasks.append(task)
                for task in due_tasks:
                    del self.active_reminders[task.task_id]

            for task in due_tasks:
                logger.info(f"到达提醒时间，执行提醒: {task.task_id}")
                self._do_remind(task)

            time.sleep(1)

    def _do_remind(self, task: ReminderTask):
        """执行提醒：语音类型通过 Telegram 发送语音消息，文本类型走原消息处理流程。"""
        try:
            prompt = self._get_reminder_prompt(task.content)
            logger.debug(
                f"生成提醒消息 - 用户: {task.sender_name}, "
                f"类型: {task.reminder_type}, 提示词: {prompt}"
            )

            if task.reminder_type == "voice":
                # 通过 Telegram Bot 发送语音消息（替代原有微信语音通话）
                if self.bot and self.tg_loop and task.audio_path:
                    Call(
                        bot=self.bot,
                        tg_loop=self.tg_loop,
                        chat_id=task.chat_id,
                        audio_file_path=task.audio_path
                    )
                    tts._del_audio_file(task.audio_path)
                else:
                    logger.warning(
                        "语音提醒缺少必要参数（bot/tg_loop/audio_path），"
                        "降级为文本提醒"
                    )
                    self.message_handler.handle_user_message(
                        content=prompt,
                        chat_id=task.chat_id,
                        sender_name="System",
                        username="System",
                        is_group=False
                    )
            else:
                # 文本提醒：走消息处理器，由 AI 生成个性化回复后发送
                self.message_handler.handle_user_message(
                    content=prompt,
                    chat_id=task.chat_id,
                    sender_name="System",
                    username="System",
                    is_group=False
                )

            logger.info(f"已发送提醒消息给 {task.sender_name} (chat_id={task.chat_id})")

        except Exception as e:
            logger.error(f"发送提醒消息失败: {str(e)}")

    def _remind_text_generate(self, remind_content: str, sender_name: str) -> str:
        """预生成语音提醒的文本内容（供 TTS 使用）"""
        core_mem = self.mem_service.get_core_memory(
            avatar_name=self.message_handler.current_avatar,
            user_id=sender_name
        )
        context = self.mem_service.get_recent_context(
            avatar_name=self.message_handler.current_avatar,
            user_id=sender_name
        )
        sys_prompt = (
            f"你将进行角色扮演，请你同用户进行符合人设的交流沟通。你的人设如下：\n\n"
            f"{self.message_handler.prompt_content}\n\n"
            f"另外，作为一个仿真的角色扮演者，你需要掌握一些你不一定用到的、"
            f"但是十分重要的知识：{core_mem}。你的每次回应都不应该违反这些知识！"
        )
        messages = [
            {"role": "system", "content": sys_prompt},
            *context[-self.message_handler.max_groups * 2:]
        ]
        messages.append({
            "role": "system",
            "content": (
                f"现在提醒时间到了，用户之前设定的提示内容为{remind_content}。"
                f"请以你的人设中的身份主动找用户聊天。保持角色设定的一致性和上下文的连贯性。"
            )
        })
        request_config = {
            "model": self.message_handler.model,
            "messages": messages,
            "temperature": self.message_handler.temperature,
            "max_tokens": self.message_handler.max_token,
        }
        response = self.llm_service.client.chat.completions.create(**request_config)
        return response.choices[0].message.content

    def add_reminder(self, chat_id: str, target_time: datetime, content: str,
                     sender_name: str, reminder_type: str = "text"):
        """添加提醒任务。"""
        try:
            task_id = f"reminder_{chat_id}_{datetime.now().timestamp()}"
            task = ReminderTask(task_id, chat_id, target_time, content, sender_name, reminder_type)

            if reminder_type == "voice":
                logger.info("检测到语音提醒任务，预生成回复中")
                remind_text = self._remind_text_generate(
                    remind_content=content,
                    sender_name=sender_name
                )
                logger.info(f"预生成回复: {tts._clear_tts_text(remind_text)}")
                logger.info("生成语音中")
                audio_file_path = tts._generate_audio_file(tts._clear_tts_text(remind_text))

                if audio_file_path is None:
                    # 语音生成失败，降级为文本提醒
                    logger.warning("提醒任务语音生成失败，将替换为文本提醒任务")
                    fallback_task = ReminderTask(
                        task_id, chat_id, target_time, content,
                        sender_name, reminder_type="text"
                    )
                    with self._lock:
                        self.active_reminders[task_id] = fallback_task
                else:
                    task.audio_path = audio_file_path
                    logger.info("提醒任务语音生成完成")
                    with self._lock:
                        self.active_reminders[task_id] = task
            else:
                with self._lock:
                    self.active_reminders[task_id] = task

            logger.info(
                f"提醒任务已添加。提醒时间: {target_time}, 内容: {content}，"
                f"用户：{sender_name}，类型：{reminder_type}"
            )

        except Exception as e:
            logger.error(f"添加提醒任务失败: {str(e)}")

    def cancel_reminder(self, task_id: str) -> bool:
        """取消指定提醒任务。"""
        with self._lock:
            if task_id in self.active_reminders:
                del self.active_reminders[task_id]
                logger.info(f"提醒任务已取消: {task_id}")
                return True
            return False

    def list_reminders(self) -> List[Dict]:
        """返回当前所有活跃提醒任务的列表。"""
        with self._lock:
            return [{
                'task_id': task_id,
                'chat_id': task.chat_id,
                'target_time': task.target_time.isoformat(),
                'content': task.content,
                'sender_name': task.sender_name,
                'reminder_type': task.reminder_type
            } for task_id, task in self.active_reminders.items()]

    def _get_reminder_prompt(self, content: str) -> str:
        return (
            f"现在提醒时间到了，用户之前设定的提示内容为{content}。"
            f"请以你的人设中的身份主动找用户聊天。保持角色设定的一致性和上下文的连贯性。"
        )


'''
单独对模块进行调试时，可以使用该代码
'''
if __name__ == '__main__':
    pass
