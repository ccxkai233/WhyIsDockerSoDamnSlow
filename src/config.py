import configparser
import os
import shutil
import sys

# 配置文件名
CONFIG_FILE = "config.ini"
EXAMPLE_CONFIG_FILE = "config.ini.example"

def load_config():
    """加载配置，如果配置文件不存在则从模板创建。"""
    if not os.path.exists(CONFIG_FILE):
        if not os.path.exists(EXAMPLE_CONFIG_FILE):
            raise FileNotFoundError(
                f"错误: 配置文件 '{CONFIG_FILE}' 和模板 '{EXAMPLE_CONFIG_FILE}' 都不存在。"
            )
        print(f"提示: 未找到 '{CONFIG_FILE}', 将从 '{EXAMPLE_CONFIG_FILE}' 创建。")
        shutil.copy(EXAMPLE_CONFIG_FILE, CONFIG_FILE)

    config = configparser.ConfigParser()
    config.read(CONFIG_FILE, encoding='utf-8')
    return config

# 加载配置以供其他模块使用
try:
    config = load_config()
    PRIVATE_REGISTRY = config.get('Registry', 'private_registry')
    CACHE_REGISTRY = config.get('Registry', 'cache_registry')
    REGISTRY_USER = config.get('Registry', 'registry_user', fallback=None)
    REGISTRY_PASS = config.get('Registry', 'registry_pass', fallback=None)
    SSH_HOST = config.get('SSH', 'host')
    SSH_PORT = config.getint('SSH', 'port')
    SSH_USER = config.get('SSH', 'user')
    SSH_KEY_PATH = config.get('SSH', 'key_path')
    SSH_KEY_PASS = config.get('SSH', 'key_pass', fallback=None)

except (configparser.NoSectionError, configparser.NoOptionError, FileNotFoundError) as e:
    print(f"配置文件错误: {e}")
    sys.exit(1)