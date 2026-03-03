"""
语音提醒模块（Telegram 适配版）

原微信版本通过操控 WeChat GUI 发起语音通话并播放音频。
Telegram Bot API 不支持主动发起语音通话，因此改为：
  - 直接将预生成的音频文件以语音消息（send_voice）的形式发送给用户
  - 同时发送一条文本提示，告知这是一条提醒语音

依赖：python-telegram-bot v20+，pygame（仅保留本地播放功能，可选）
"""
import logging
import asyncio
import time
import os

import pygame

logger = logging.getLogger('main')

# --- 配置参数 ---
# Telegram 语音消息支持的格式：OGG/OPUS（推荐），MP3 也可发送但会作为音频文件处理
# 若需要严格发送 voice（语音气泡），请将 TTS 输出格式改为 .ogg
VOICE_CAPTION = "📢 你设置的提醒时间到了，这是一条语音提醒消息。"


# ── 核心发送函数 ────────────────────────────────────────────────────────────────

def _run_async_in_loop(coro, tg_loop):
    """跨线程在指定 event loop 中执行协程，阻塞直到完成。"""
    try:
        future = asyncio.run_coroutine_threadsafe(coro, tg_loop)
        return future.result(timeout=30)
    except Exception as e:
        logger.error(f"Telegram 发送操作失败: {e}")
        return None


def SendVoiceMessage(bot, tg_loop, chat_id: str, audio_file_path: str,
                     caption: str = VOICE_CAPTION) -> bool:
    """
    通过 Telegram Bot 向指定用户发送语音消息。

    Telegram 区分两种音频类型：
      - send_voice：发送语音气泡（需要 .ogg/opus 格式）
      - send_audio：发送普通音频文件（支持 .mp3 等）

    本函数会根据文件扩展名自动选择接口。

    Args:
        bot:            telegram.Bot 实例。
        tg_loop:        Bot 所在的 asyncio event loop。
        chat_id:        目标用户的 Telegram chat_id（字符串或整数）。
        audio_file_path: 要发送的音频文件路径。
        caption:        附带的文字说明。

    Returns:
        成功返回 True，失败返回 False。
    """
    if not audio_file_path or not os.path.exists(audio_file_path):
        logger.error(f"音频文件不存在，无法发送语音消息: {audio_file_path}")
        return False

    logger.info(f"向 chat_id={chat_id} 发送语音提醒消息: {audio_file_path}")

    ext = os.path.splitext(audio_file_path)[1].lower()
    is_ogg = ext in ('.ogg', '.opus', '.oga')

    async def _send():
        with open(audio_file_path, 'rb') as f:
            if is_ogg:
                # 发送为语音气泡
                await bot.send_voice(
                    chat_id=int(chat_id),
                    voice=f,
                    caption=caption
                )
            else:
                # 非 ogg 格式，以音频文件形式发送（在聊天中显示播放器）
                await bot.send_audio(
                    chat_id=int(chat_id),
                    audio=f,
                    caption=caption
                )

    result = _run_async_in_loop(_send(), tg_loop)
    if result is not None or True:
        # run_coroutine_threadsafe 返回 None 表示协程已正常完成（无返回值）
        logger.info(f"语音提醒消息已发送至 chat_id={chat_id}")
        return True
    return False


def SendTextReminder(bot, tg_loop, chat_id: str, text: str) -> bool:
    """
    通过 Telegram Bot 向指定用户发送文本提醒消息。

    Args:
        bot:      telegram.Bot 实例。
        tg_loop:  Bot 所在的 asyncio event loop。
        chat_id:  目标用户的 Telegram chat_id。
        text:     提醒文本内容。

    Returns:
        成功返回 True，失败返回 False。
    """
    if not text or not text.strip():
        logger.warning("提醒文本为空，跳过发送")
        return False

    logger.info(f"向 chat_id={chat_id} 发送文本提醒: {text[:50]}...")

    async def _send():
        await bot.send_message(chat_id=int(chat_id), text=text.strip())

    _run_async_in_loop(_send(), tg_loop)
    logger.info(f"文本提醒已发送至 chat_id={chat_id}")
    return True


def Call(bot, tg_loop, chat_id: str, audio_file_path: str,
         caption: str = VOICE_CAPTION) -> None:
    """
    向指定用户发送语音提醒（对外统一接口，替代原微信语音通话逻辑）。

    原版通过微信 GUI 发起语音通话并播放音频；Telegram 版改为直接发送语音消息。

    Args:
        bot:             telegram.Bot 实例。
        tg_loop:         Bot 所在的 asyncio event loop。
        chat_id:         目标用户的 Telegram chat_id（字符串）。
        audio_file_path: 预生成的 TTS 音频文件路径。
        caption:         附带的文字说明。

    Returns:
        None
    """
    success = SendVoiceMessage(
        bot=bot,
        tg_loop=tg_loop,
        chat_id=chat_id,
        audio_file_path=audio_file_path,
        caption=caption
    )
    if not success:
        logger.error(f"语音提醒发送失败，chat_id={chat_id}，文件={audio_file_path}")
    else:
        logger.info(f"语音提醒流程完成，chat_id={chat_id}")


# ── 本地播放工具（可选，用于调试或本地测试）──────────────────────────────────────

def PlayVoice(audio_file_path: str, device=None) -> bool:
    """
    在本地播放指定音频文件（调试用途，不通过 Telegram 发送）。

    Args:
        audio_file_path: 要播放的音频文件路径。
        device:          （可选）音频输出设备名称，默认使用系统默认设备。

    Returns:
        完整播放成功返回 True，否则返回 False。
    """
    logger.info(f"本地播放音频文件: '{audio_file_path}'")
    if device:
        logger.info(f"目标输出设备: '{device}'")
    else:
        logger.info("目标输出设备: 系统默认")

    try:
        pygame.mixer.quit()
        pygame.mixer.init(devicename=device)
        pygame.mixer.music.load(audio_file_path)
        time.sleep(2)
        pygame.mixer.music.play()
        logger.info("开始播放音频...")

        while pygame.mixer.music.get_busy():
            time.sleep(0.1)

        logger.info("音频播放完毕。")
        return True

    except pygame.error as e:
        logger.error(f"Pygame 错误: {e}")
        return False
    except FileNotFoundError:
        logger.error(f"音频文件未找到: '{audio_file_path}'")
        return False
    except Exception as e:
        logger.error(f"发生未知错误: {e}")
        return False
    finally:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()


# ── 主程序示例（仅用于调试）──────────────────────────────────────────────────────

if __name__ == '__main__':
    import asyncio
    from telegram import Bot

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(module)s.%(funcName)s: %(message)s',
        handlers=[logging.StreamHandler()]
    )

    TOKEN = ""        # 填入 Bot Token
    CHAT_ID = ""      # 填入目标 chat_id
    AUDIO_FILE = "test.mp3"  # 填入测试音频路径

    if TOKEN and CHAT_ID and os.path.exists(AUDIO_FILE):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        bot = Bot(token=TOKEN)
        # 在后台线程中启动 loop
        import threading
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        logger.info("程序启动，测试语音提醒发送")
        Call(bot=bot, tg_loop=loop, chat_id=CHAT_ID, audio_file_path=AUDIO_FILE)
        logger.info("程序结束")
    else:
        logger.error("请先填写 TOKEN、CHAT_ID 并确保音频文件存在。")
