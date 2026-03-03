"""
表情包处理模块
负责处理表情包相关功能，包括:
- 表情标签识别
- 表情包选择
- 文件管理
"""

import os
import random
import logging
from typing import Optional
from data.config import config

logger = logging.getLogger('main')


class EmojiHandler:
    def __init__(self, root_dir):
        self.root_dir = root_dir
        # 表情包目录：人设目录下的 emojis 子目录
        self.emoji_dir = os.path.join(root_dir, config.behavior.context.avatar_dir, "emojis")

        # 支持的表情类型列表（
        self.emotion_types = [
            'happy', 'sad', 'angry', 'neutral', 'love', 'funny', 'cute', 'bored', 'shy',
            'embarrassed', 'sleepy', 'lonely', 'hungry', 'comfort', 'surprise', 'confused',
            'playful', 'excited', 'tease', 'hot', 'speechless', 'scared', 'emo_1',
            'emo_2', 'emo_3', 'emo_4', 'emo_5', 'afraid', 'amused', 'anxious',
            'confident', 'cold', 'suspicious', 'loving', 'curious', 'envious',
            'jealous', 'miserable', 'stupid', 'sick', 'ashamed', 'withdrawn',
            'indifferent', 'sorry', 'determined', 'crazy', 'bashful', 'depressed',
            'enraged', 'frightened', 'interested', 'hopeful', 'regretful', 'stubborn',
            'thirsty', 'guilty', 'nervous', 'disgusted', 'proud', 'ecstatic',
            'frustrated', 'hurt', 'tired', 'smug', 'thoughtful', 'pained', 'optimistic',
            'relieved', 'puzzled', 'shocked', 'joyful', 'skeptical', 'bad', 'worried'
        ]

    def extract_emotion_tags(self, text: str) -> list:
        """从文本中提取表情标签（如 [happy]）"""
        tags = []
        start = 0
        while True:
            start = text.find('[', start)
            if start == -1:
                break
            end = text.find(']', start)
            if end == -1:
                break
            tag = text[start+1:end].lower()
            if tag in self.emotion_types:
                tags.append(tag)
                logger.info(f"检测到表情标签: {tag}")
            start = end + 1
        return tags

    def get_emoji_for_emotion(self, emotion_type: str) -> Optional[str]:
        """根据情感类型获取对应表情包的本地文件路径"""
        try:
            target_dir = os.path.join(self.emoji_dir, emotion_type)
            logger.info(f"查找表情包目录: {target_dir}")

            if not os.path.exists(target_dir):
                logger.warning(f"情感目录不存在: {target_dir}")
                return None

            emoji_files = [f for f in os.listdir(target_dir)
                           if f.lower().endswith(('.gif', '.jpg', '.png', '.jpeg', '.webp'))]

            if not emoji_files:
                logger.warning(f"目录中未找到表情包: {target_dir}")
                return None

            selected = random.choice(emoji_files)
            emoji_path = os.path.join(target_dir, selected)
            logger.info(f"已选择 {emotion_type} 表情包: {emoji_path}")
            return emoji_path

        except Exception as e:
            logger.error(f"获取表情包失败: {str(e)}")
            return None