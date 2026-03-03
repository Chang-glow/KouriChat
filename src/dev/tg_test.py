import os

from utils.logger import LoggerConfig
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters


# [DEBUG] 加载配置
BOT_TOKEN = '8613628115:AAF0zVp4q_bjuL24Mnnidz9Yh9bCwTXrBtw'
PROXY_URL = 'http://127.0.0.1:7890'

# 初始化日志
root_dir = os.path.dirname(os.path.abspath(__file__))
log_config = LoggerConfig(root_dir)
logger = log_config.setup_logger(name=__file__)


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    a1 = context
    if a1 is None:
        print(a1)
    logger.info(f"收到消息 | ChatID: {update.effective_chat.id} | 内容: {user_text}")
    await update.message.reply_text(f"我收到啦，你刚刚给我发了:{user_text}")


def main():
    # 清理日志
    log_config.cleanup_old_logs(days=7)

    logger.info("尝试链接到telegram")

    try:
        # 构建Application并配置代理
        application = ApplicationBuilder().token(BOT_TOKEN).proxy(PROXY_URL).get_updates_proxy(PROXY_URL).build()

        # 注册处理器
        echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), echo)
        application.add_handler(echo_handler)

        logger.info("--- 正在启动 TG 测试机器人 ---")
        application.run_polling()
    except Exception as e:
        logger.error(f"启动失败:{str(e)}")


if __name__ == "__main__":
    main()
