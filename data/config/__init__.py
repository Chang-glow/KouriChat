import os
import json
import logging
import shutil
import difflib
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# ==================== 数据类定义 ====================

@dataclass
class BotSettings:
    """Telegram Bot 配置"""
    token: str
    name: str = ""                 # Bot 用户名（不含 @），留空则启动时自动获取
    proxy_url: str = ""             # 代理地址（如 http://127.0.0.1:1080）

@dataclass
class GroupChatConfigItem:
    id: str
    group_name: str                 # 在 Telegram 中应填写群组数字 ID
    avatar: str
    triggers: List[str]
    enable_at_trigger: bool = True

@dataclass
class UserSettings:
    telegram_chat_ids: List[str]    # 允许响应的 Chat ID 列表（私聊为正整数，群聊为负整数）
    group_chat_config: List[GroupChatConfigItem] = None

    def __post_init__(self):
        if self.group_chat_config is None:
            self.group_chat_config = []

@dataclass
class LLMSettings:
    api_key: str
    base_url: str
    model: str
    max_tokens: int
    temperature: float
    auto_model_switch: bool = False

@dataclass
class ImageRecognitionSettings:
    api_key: str
    base_url: str
    temperature: float
    model: str

@dataclass
class ImageGenerationSettings:
    model: str
    temp_dir: str

@dataclass
class TextToSpeechSettings:
    tts_api_key: str
    tts_model_id: str
    voice_dir: str

@dataclass
class MediaSettings:
    image_recognition: ImageRecognitionSettings
    image_generation: ImageGenerationSettings
    text_to_speech: TextToSpeechSettings

@dataclass
class AutoMessageSettings:
    content: str
    min_hours: float
    max_hours: float

@dataclass
class QuietTimeSettings:
    start: str
    end: str

@dataclass
class ContextSettings:
    max_groups: int
    avatar_dir: str

@dataclass
class MessageQueueSettings:
    timeout: int

@dataclass
class TaskSettings:
    task_id: str
    chat_id: str
    content: str
    schedule_type: str
    schedule_time: str
    is_active: bool

@dataclass
class ScheduleSettings:
    tasks: List[TaskSettings]

@dataclass
class BehaviorSettings:
    auto_message: AutoMessageSettings
    quiet_time: QuietTimeSettings
    context: ContextSettings
    schedule_settings: ScheduleSettings
    message_queue: MessageQueueSettings

@dataclass
class AuthSettings:
    admin_password: str

@dataclass
class NetworkSearchSettings:
    search_enabled: bool
    weblens_enabled: bool
    api_key: str
    base_url: str

@dataclass
class IntentRecognitionSettings:
    api_key: str
    base_url: str
    model: str
    temperature: float

# ==================== Config 类 ====================

class Config:
    def __init__(self):
        self.bot: BotSettings
        self.webhook_enabled: bool = True
        self.webhook_listen_port: int = 9000
        self.webhook_url: str = ""
        self.user: UserSettings
        self.llm: LLMSettings
        self.media: MediaSettings
        self.behavior: BehaviorSettings
        self.auth: AuthSettings
        self.network_search: NetworkSearchSettings
        self.intent_recognition: IntentRecognitionSettings
        self.version: str = "1.0.0"
        self.load_config()

    @property
    def config_dir(self) -> str:
        return os.path.dirname(__file__)

    @property
    def config_path(self) -> str:
        return os.path.join(self.config_dir, 'config.json')

    @property
    def config_template_path(self) -> str:
        return os.path.join(self.config_dir, 'config.json.template')

    @property
    def config_template_bak_path(self) -> str:
        return os.path.join(self.config_dir, 'config.json.template.bak')

    @property
    def config_backup_dir(self) -> str:
        backup_dir = os.path.join(self.config_dir, 'backups')
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
        return backup_dir

    def backup_config(self) -> str:
        if not os.path.exists(self.config_path):
            logger.warning("无法备份配置文件：文件不存在")
            return ""

        backup_filename = "config_backup.json"
        backup_path = os.path.join(self.config_backup_dir, backup_filename)

        if os.path.exists(backup_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f1, \
                     open(backup_path, 'r', encoding='utf-8') as f2:
                    if f1.read() == f2.read():
                        logger.debug("配置未发生变更，跳过备份")
                        return backup_path
            except Exception as e:
                logger.error(f"比较配置文件失败: {str(e)}")

        try:
            shutil.copy2(self.config_path, backup_path)
            logger.info(f"已备份配置文件到: {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"备份配置文件失败: {str(e)}")
            return ""

    def _backup_template(self, force=False):
        if force or not os.path.exists(self.config_template_bak_path):
            try:
                shutil.copy2(self.config_template_path, self.config_template_bak_path)
                logger.info(f"已创建模板配置备份: {self.config_template_bak_path}")
                return True
            except Exception as e:
                logger.warning(f"创建模板配置备份失败: {str(e)}")
                return False
        return False

    def compare_configs(self, old_config: Dict[str, Any], new_config: Dict[str, Any], path: str = "") -> Dict[str, Any]:
        diff = {"added": {}, "removed": {}, "modified": {}}
        for key, new_value in new_config.items():
            current_path = f"{path}.{key}" if path else key
            if key not in old_config:
                diff["added"][current_path] = new_value
            elif isinstance(new_value, dict) and isinstance(old_config[key], dict):
                sub_diff = self.compare_configs(old_config[key], new_value, current_path)
                for dt in ["added", "removed", "modified"]:
                    diff[dt].update(sub_diff[dt])
            elif new_value != old_config[key]:
                diff["modified"][current_path] = {"old": old_config[key], "new": new_value}
        for key in old_config:
            current_path = f"{path}.{key}" if path else key
            if key not in new_config:
                diff["removed"][current_path] = old_config[key]
        return diff

    def generate_diff_report(self, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> str:
        old_json = json.dumps(old_config, indent=4, ensure_ascii=False).splitlines()
        new_json = json.dumps(new_config, indent=4, ensure_ascii=False).splitlines()
        diff = difflib.unified_diff(old_json, new_json, fromfile='old_config', tofile='new_config', lineterm='')
        return '\n'.join(diff)

    def merge_configs(self, current: dict, template: dict, old_template: dict = None) -> dict:
        result = current.copy()
        for key, value in template.items():
            if key not in current:
                result[key] = value
            elif isinstance(value, dict) and isinstance(current[key], dict):
                old_value = old_template.get(key, {}) if old_template else None
                result[key] = self.merge_configs(current[key], value, old_value)
            elif old_template and key in old_template and current[key] == old_template[key] and value != old_template[key]:
                logger.debug(f"字段 '{key}' 更新为新模板值")
                result[key] = value
        return result

    def save_config(self, config_data: dict) -> bool:
        try:
            self.backup_config()
            with open(self.config_path, 'r', encoding='utf-8') as f:
                current_config = json.load(f)
            for key, value in config_data.items():
                if key in current_config and isinstance(current_config[key], dict) and isinstance(value, dict):
                    self._recursive_update(current_config[key], value)
                else:
                    current_config[key] = value
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(current_config, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"保存配置失败: {str(e)}")
            return False

    def _recursive_update(self, target: dict, source: dict):
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._recursive_update(target[key], value)
            else:
                target[key] = value

    def _check_and_update_config(self) -> None:
        try:
            if not os.path.exists(self.config_template_path):
                logger.warning(f"模板配置文件不存在: {self.config_template_path}")
                return
            with open(self.config_path, 'r', encoding='utf-8') as f:
                current_config = json.load(f)
            with open(self.config_template_path, 'r', encoding='utf-8') as f:
                template_config = json.load(f)
            self._backup_template()
            old_template_config = None
            if os.path.exists(self.config_template_bak_path):
                try:
                    with open(self.config_template_bak_path, 'r', encoding='utf-8') as f:
                        old_template_config = json.load(f)
                except Exception as e:
                    logger.warning(f"读取备份模板失败: {str(e)}")
            diff = self.compare_configs(current_config, template_config)
            if any(diff.values()):
                logger.info("检测到配置需要更新")
                backup_path = self.backup_config()
                if backup_path:
                    logger.info(f"已备份原配置到: {backup_path}")
                updated_config = self.merge_configs(current_config, template_config, old_template_config)
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    json.dump(updated_config, f, indent=4, ensure_ascii=False)
                logger.info("配置文件已更新")
            else:
                logger.debug("配置文件无需更新")
        except Exception as e:
            logger.error(f"检查配置更新失败: {str(e)}")
            raise

    def load_config(self) -> None:
        try:
            if not os.path.exists(self.config_path):
                if os.path.exists(self.config_template_path):
                    logger.info("配置文件不存在，从模板创建")
                    shutil.copy2(self.config_template_path, self.config_path)
                    self._backup_template()
                else:
                    raise FileNotFoundError("配置和模板文件都不存在")

            self._check_and_update_config()

            with open(self.config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                categories = config_data['categories']

                # ---------- Bot 配置 ----------
                bot_data = categories.get('bot_settings', {}).get('settings', {})
                self.bot = BotSettings(
                    token=bot_data.get('token', {}).get('value', ''),
                    name=bot_data.get('name', {}).get('value', ''),
                    proxy_url=bot_data.get('proxy_url', {}).get('value', '')
                )

                # ---------- Webhook 设置 ----------
                webhook_data = categories.get('webhook_settings', {}).get('settings', {})
                self.webhook_enabled = webhook_data.get('enabled', {}).get('value', True)
                self.webhook_listen_port = webhook_data.get('listen_port', {}).get('value', 9000)
                self.webhook_url = webhook_data.get('url', {}).get('value', '')

                # ---------- 用户设置 ----------
                user_data = categories['user_settings']['settings']
                # 允许响应的 Chat ID 列表
                telegram_chat_ids_raw = user_data.get('telegram_chat_ids', {}).get('value', [])
                if isinstance(telegram_chat_ids_raw, list):
                    telegram_chat_ids = [str(x) for x in telegram_chat_ids_raw]
                elif isinstance(telegram_chat_ids_raw, str):
                    telegram_chat_ids = [x.strip() for x in telegram_chat_ids_raw.split(',') if x.strip()]
                else:
                    telegram_chat_ids = []

                # 群聊配置
                group_chat_config_data = user_data.get('group_chat_config', {}).get('value', [])
                group_chat_configs = []
                if isinstance(group_chat_config_data, list):
                    for config_item in group_chat_config_data:
                        if isinstance(config_item, dict) and all(k in config_item for k in ['id', 'groupName', 'avatar', 'triggers']):
                            group_chat_configs.append(GroupChatConfigItem(
                                id=config_item['id'],
                                group_name=config_item['groupName'],
                                avatar=config_item['avatar'],
                                triggers=config_item.get('triggers', []),
                                enable_at_trigger=config_item.get('enableAtTrigger', True)
                            ))

                self.user = UserSettings(
                    telegram_chat_ids=telegram_chat_ids,
                    group_chat_config=group_chat_configs
                )

                # ---------- LLM 设置 ----------
                llm_data = categories['llm_settings']['settings']
                self.llm = LLMSettings(
                    api_key=llm_data['api_key'].get('value', ''),
                    base_url=llm_data['base_url'].get('value', ''),
                    model=llm_data['model'].get('value', ''),
                    max_tokens=int(llm_data['max_tokens'].get('value', 0)),
                    temperature=float(llm_data['temperature'].get('value', 0)),
                    auto_model_switch=bool(llm_data['auto_model_switch'].get('value', False))
                )

                # ---------- 媒体设置 ----------
                media_data = categories['media_settings']['settings']
                self.media = MediaSettings(
                    image_recognition=ImageRecognitionSettings(
                        api_key=media_data['image_recognition']['api_key'].get('value', ''),
                        base_url=media_data['image_recognition']['base_url'].get('value', ''),
                        temperature=float(media_data['image_recognition']['temperature'].get('value', 0)),
                        model=media_data['image_recognition']['model'].get('value', '')
                    ),
                    image_generation=ImageGenerationSettings(
                        model=media_data['image_generation']['model'].get('value', ''),
                        temp_dir=media_data['image_generation']['temp_dir'].get('value', '')
                    ),
                    text_to_speech=TextToSpeechSettings(
                        tts_api_key=media_data['text_to_speech']['tts_api_key'].get('value', ''),
                        tts_model_id=media_data['text_to_speech']['tts_model_id'].get('value', ''),
                        voice_dir=media_data['text_to_speech']['voice_dir'].get('value', '')
                    )
                )

                # ---------- 行为设置 ----------
                behavior_data = categories['behavior_settings']['settings']
                auto_message_data = behavior_data['auto_message']
                auto_message_countdown = auto_message_data.get('countdown', {})
                quiet_time_data = behavior_data['quiet_time']
                context_data = behavior_data['context']
                message_queue_data = behavior_data.get('message_queue', {})
                message_queue_timeout = message_queue_data.get('timeout', {}).get('value', 8)

                avatar_dir = context_data['avatar_dir'].get('value', '')
                if not avatar_dir.startswith('data/avatars/'):
                    avatar_dir = f"data/avatars/{avatar_dir.split('/')[-1]}"

                # 定时任务
                schedule_tasks = []
                if 'schedule_settings' in categories:
                    schedule_data = categories['schedule_settings']
                    if 'settings' in schedule_data and 'tasks' in schedule_data['settings']:
                        tasks_data = schedule_data['settings']['tasks'].get('value', [])
                        for task in tasks_data:
                            if all(k in task for k in ['task_id', 'chat_id', 'content', 'schedule_type', 'schedule_time']):
                                schedule_tasks.append(TaskSettings(
                                    task_id=task['task_id'],
                                    chat_id=task['chat_id'],
                                    content=task['content'],
                                    schedule_type=task['schedule_type'],
                                    schedule_time=task['schedule_time'],
                                    is_active=task.get('is_active', True)
                                ))

                self.behavior = BehaviorSettings(
                    auto_message=AutoMessageSettings(
                        content=auto_message_data['content'].get('value', ''),
                        min_hours=float(auto_message_countdown.get('min_hours', {}).get('value', 0)),
                        max_hours=float(auto_message_countdown.get('max_hours', {}).get('value', 0))
                    ),
                    quiet_time=QuietTimeSettings(
                        start=quiet_time_data['start'].get('value', ''),
                        end=quiet_time_data['end'].get('value', '')
                    ),
                    context=ContextSettings(
                        max_groups=int(context_data['max_groups'].get('value', 0)),
                        avatar_dir=avatar_dir
                    ),
                    schedule_settings=ScheduleSettings(tasks=schedule_tasks),
                    message_queue=MessageQueueSettings(timeout=int(message_queue_timeout))
                )

                # ---------- 认证设置 ----------
                auth_data = categories.get('auth_settings', {}).get('settings', {})
                self.auth = AuthSettings(
                    admin_password=auth_data.get('admin_password', {}).get('value', '')
                )

                # ---------- 网络搜索 ----------
                network_search_data = categories.get('network_search_settings', {}).get('settings', {})
                self.network_search = NetworkSearchSettings(
                    search_enabled=network_search_data.get('search_enabled', {}).get('value', False),
                    weblens_enabled=network_search_data.get('weblens_enabled', {}).get('value', False),
                    api_key=network_search_data.get('api_key', {}).get('value', ''),
                    base_url=network_search_data.get('base_url', {}).get('value', 'https://api.kourichat.com/v1')
                )

                # ---------- 意图识别 ----------
                intent_recognition_data = categories.get('intent_recognition_settings', {}).get('settings', {})
                self.intent_recognition = IntentRecognitionSettings(
                    api_key=intent_recognition_data.get('api_key', {}).get('value', ''),
                    base_url=intent_recognition_data.get('base_url', {}).get('value', 'https://api.kourichat.com/v1'),
                    model=intent_recognition_data.get('model', {}).get('value', 'kourichat-v3'),
                    temperature=float(intent_recognition_data.get('temperature', {}).get('value', 0.1))
                )

                logger.info("配置加载完成")

        except Exception as e:
            logger.error(f"加载配置失败: {str(e)}")
            raise

    def update_password(self, password: str) -> bool:
        try:
            config_data = {
                'categories': {
                    'auth_settings': {
                        'settings': {
                            'admin_password': {'value': password}
                        }
                    }
                }
            }
            return self.save_config(config_data)
        except Exception as e:
            logger.error(f"更新密码失败: {str(e)}")
            return False


# 创建全局配置实例
config = Config()

# 为保持向后兼容性，导出常用变量（建议新代码使用 config 对象直接访问）
DEEPSEEK_API_KEY = config.llm.api_key
DEEPSEEK_BASE_URL = config.llm.base_url
MODEL = config.llm.model
MAX_TOKEN = config.llm.max_tokens
TEMPERATURE = config.llm.temperature
VISION_API_KEY = config.media.image_recognition.api_key
VISION_BASE_URL = config.media.image_recognition.base_url
VISION_TEMPERATURE = config.media.image_recognition.temperature
IMAGE_MODEL = config.media.image_generation.model
TEMP_IMAGE_DIR = config.media.image_generation.temp_dir
MAX_GROUPS = config.behavior.context.max_groups
VOICE_DIR = config.media.text_to_speech.voice_dir
AUTO_MESSAGE = config.behavior.auto_message.content
MIN_COUNTDOWN_HOURS = config.behavior.auto_message.min_hours
MAX_COUNTDOWN_HOURS = config.behavior.auto_message.max_hours
QUIET_TIME_START = config.behavior.quiet_time.start
QUIET_TIME_END = config.behavior.quiet_time.end
NETWORK_SEARCH_ENABLED = config.network_search.search_enabled
WEBLENS_ENABLED = config.network_search.weblens_enabled
NETWORK_SEARCH_API_KEY = config.network_search.api_key
NETWORK_SEARCH_BASE_URL = config.network_search.base_url
