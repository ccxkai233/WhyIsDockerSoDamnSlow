import re
from .config import CACHE_REGISTRY

def transform_image_name(image_name):
    """
    将 Docker 镜像名转换为使用私有仓库的地址。
    - 官方镜像 (如 'python:3.9') 变为 'PRIVATE_REGISTRY/library/python:3.9'。
    - 用户镜像 (如 'bitnami/redis') 变为 'PRIVATE_REGISTRY/bitnami/redis'。
    - 其他仓库的镜像 (如 'gcr.io/...') 保持不变。
    """
    # 检查镜像是是否已经指定了仓库地址 (名称第一部分包含 . 或 :)
    parts = image_name.split('/')
    if len(parts) > 1 and ('.' in parts[0] or ':' in parts[0]):
        return image_name, False # 返回原始名称和表示未转换的标志

    # 如果是官方镜像 (名称中不含'/')，则添加 'library/' 前缀
    if '/' not in image_name:
        normalized_image = f"library/{image_name}"
    else:
        normalized_image = image_name

    accelerated_image = f"{CACHE_REGISTRY}/{normalized_image}"
    return accelerated_image, True # 返回转换后的名称和表示已转换的标志

def accelerate_command(command):
    """
    解析命令，将镜像名替换为加速后的版本。
    支持 'docker pull <镜像>' 和 'FROM <镜像>' 指令。
    """
    # 匹配 'docker pull' 命令
    pull_match = re.search(r"^(docker\s+pull\s+)([\w./:-]+)$", command.strip(), re.IGNORECASE)
    if pull_match:
        prefix = pull_match.group(1)
        image_name = pull_match.group(2)
        new_image_name, _ = transform_image_name(image_name)
        return f"{prefix}{new_image_name}"

    # 匹配 Dockerfile 的 'FROM' 指令
    from_match = re.search(
        r"^\s*(FROM\s+(?:--platform=[\w/]+\s+)?)([\w./:-]+)(\s*AS\s+[\w-]+)?\s*$",
        command,
        re.IGNORECASE
    )
    if from_match:
        prefix = from_match.group(1)
        image_name = from_match.group(2)
        suffix = from_match.group(3) or ""
        new_image_name, _ = transform_image_name(image_name)
        return f"{prefix}{new_image_name}{suffix}"

    return command

def get_image_name_from_input(command):
    """从用户输入中智能提取出镜像名称。"""
    original_command = command.strip()
    
    # 尝试从 'docker pull' 或 'FROM' 中提取镜像名
    pull_match = re.search(r"docker\s+pull\s+([\w./:-]+)", original_command, re.IGNORECASE)
    if pull_match:
        return pull_match.group(1)
    
    from_match = re.search(r"FROM\s+(?:--platform=[\w/]+\s+)?([\w./:-]+)", original_command, re.IGNORECASE)
    if from_match:
        return from_match.group(1)
        
    # 如果都不是，则假定整个输入就是镜像名
    return original_command

def parse_dockerfile(content):
    """
    解析 Dockerfile 内容，提取所有 FROM 指令中的基础镜像。
    会忽略 ARG 定义的变量。
    """
    # 正则表达式匹配 'FROM <image_name>'，同时处理 AS 和 --platform
    # 确保不匹配以 ARG 开头的行
    from_pattern = re.compile(r"^\s*FROM\s+(?:--platform=[\w/]+\s+)?([\w./:-]+)", re.IGNORECASE | re.MULTILINE)
    
    images = from_pattern.findall(content)
    
    # 去重并返回
    return list(dict.fromkeys(images))
def accelerate_dockerfile_content(content):
    """
    接收 Dockerfile 的完整内容，将其中的所有 FROM 镜像名替换为加速后的地址。
    """
    def replace_from(match):
        # match.group(1) is the part like "FROM --platform=linux/amd64 "
        # match.group(2) is the image name "python:3.10-slim"
        # match.group(3) is the part like " AS builder" or None
        prefix = match.group(1)
        image_name = match.group(2)
        suffix = match.group(3) or ""
        
        accelerated_image, _ = transform_image_name(image_name)
        return f"{prefix}{accelerated_image}{suffix}"

    # This regex captures the parts of the FROM line more robustly
    from_pattern = re.compile(
        r"^(FROM\s+(?:--platform=[\w/]+\s+)?)([\w./:-]+)((?:\s+AS\s+[\w-]+)?)$", 
        re.IGNORECASE | re.MULTILINE
    )
    
    return from_pattern.sub(replace_from, content)