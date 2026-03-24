FROM python:3.11-slim

WORKDIR /app

# 安装 pygame 系统依赖（音频支持）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsdl2-2.0-0 \
    libsdl2-mixer-2.0-0 \
    libsdl2-image-2.0-0 \
    libsdl2-ttf-2.0-0 \
    curl \
    procps \
    net-tools \
    iproute2 \
    vim-tiny \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装 Python 包
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制整个项目
COPY . .

# 设置音频哑驱动，避免 pygame 报错
ENV SDL_AUDIODRIVER=dummy

# 暴露两个端口：WebUI（8502）和机器人 Webhook（9000）
EXPOSE 8502 9000

# 启动 WebUI，它内部会通过 subprocess 启动机器人
CMD ["python", "run_config_web.py"]

