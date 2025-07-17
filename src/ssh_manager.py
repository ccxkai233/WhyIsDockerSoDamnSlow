import paramiko
import os
import uuid
import tarfile
import tempfile
from .config import (
    SSH_HOST, SSH_PORT, SSH_USER, SSH_KEY_PATH, SSH_KEY_PASS,
    PRIVATE_REGISTRY, REGISTRY_USER, REGISTRY_PASS
)
import posixpath

class SSHManager:
    def __init__(self, logger_func=print):
        self.ssh = None
        self.sftp = None
        self.logger = logger_func

    def connect(self):
        """建立 SSH 连接。"""
        try:
            self.logger(f"--> 正在使用密钥 {SSH_KEY_PATH} 连接到 {SSH_USER}@{SSH_HOST}:{SSH_PORT}...")
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            self.ssh.connect(
                hostname=SSH_HOST,
                port=SSH_PORT,
                username=SSH_USER,
                key_filename=SSH_KEY_PATH,
                passphrase=SSH_KEY_PASS if SSH_KEY_PASS else "",
                timeout=15
            )
            # 启用压缩以提高速度
            transport = self.ssh.get_transport()
            if transport:
                transport.use_compression(True)
                self.logger("--> SSH 压缩已启用。")

            self.logger("--> SSH 连接成功！")
            return True
        except Exception as e:
            self.logger(f"SSH 连接失败: {e}")
            return False

    def execute_command(self, command):
        """在远程服务器上执行命令并记录输出，返回命令的退出状态码。"""
        if not self.ssh:
            self.logger("错误: SSH 未连接。")
            return -1
        
        self.logger(f"--> 正在远程执行: {command}")
        try:
            stdin, stdout, stderr = self.ssh.exec_command(command, timeout=3600) # 1小时超时
            
            # 实时读取标准输出
            for line in iter(stdout.readline, ""):
                self.logger(line.strip())
            
            # 检查错误输出
            error_output = stderr.read().decode('utf-8').strip()
            if error_output:
                 self.logger(f"远程错误: {error_output}")

            # 获取命令退出状态码
            exit_status = stdout.channel.recv_exit_status()
            return exit_status

        except Exception as e:
            self.logger(f"执行命令时出错: {e}")
            return -1

    def download_file(self, remote_path, local_path, progress_callback=None):
        """通过 SFTP 下载文件，并支持进度回调（优化版）。"""
        if not self.ssh:
            self.logger("错误: SSH 未连接。")
            return False
        
        self.logger(f"--> 正在通过 SFTP 下载 {remote_path} 到 {local_path}...")
        try:
            if self.sftp is None:
                self.sftp = self.ssh.open_sftp()
            
            remote_file = self.sftp.open(remote_path, 'rb')
            file_size = self.sftp.stat(remote_path).st_size
            
            # 设置更大的块大小以提高速度
            chunk_size = 2 * 1024 * 1024  # 2MB
            bytes_sent = 0

            with open(local_path, 'wb') as local_file:
                while True:
                    data = remote_file.read(chunk_size)
                    if not data:
                        break
                    local_file.write(data)
                    bytes_sent += len(data)
                    if progress_callback:
                        progress_callback(bytes_sent, file_size)
            
            remote_file.close()
            self.logger("--> SFTP 下载完成。")
            return True
        except Exception as e:
            self.logger(f"SFTP 下载失败: {e}")
            return False

    def upload_file(self, local_path, remote_path, progress_callback=None):
        """通过 SFTP 上传单个文件，并支持进度回调（优化版）。"""
        if not self.ssh:
            self.logger("错误: SSH 未连接。")
            return False

        self.logger(f"--> 正在通过 SFTP 上传文件 {local_path} 到 {remote_path}...")
        try:
            if self.sftp is None:
                self.sftp = self.ssh.open_sftp()

            file_size = os.path.getsize(local_path)
            
            # 设置更大的块大小以提高速度
            chunk_size = 2 * 1024 * 1024  # 2MB
            bytes_sent = 0

            with open(local_path, 'rb') as local_file:
                with self.sftp.open(remote_path, 'wb') as remote_file:
                    while True:
                        data = local_file.read(chunk_size)
                        if not data:
                            break
                        remote_file.write(data)
                        bytes_sent += len(data)
                        if progress_callback:
                            progress_callback(bytes_sent, file_size)

            self.logger("--> SFTP 文件上传完成。")
            return True
        except Exception as e:
            self.logger(f"SFTP 文件上传失败: {e}")
            return False

    def close(self):
        """关闭 SFTP 和 SSH 连接。"""
        if self.sftp:
            self.sftp.close()
            self.sftp = None
        if self.ssh:
            self.ssh.close()
            self.ssh = None
        self.logger("--> SSH 连接已关闭。")

    def build_and_push_project(self, local_project_path, image_tag):
        """
        打包本地项目，上传到远程服务器，构建 Docker 镜像，然后推送到私有仓库。
        """
        build_id = str(uuid.uuid4())[:8]
        remote_project_dir = f"/tmp/build-{build_id}"
        local_tar_path = os.path.join(tempfile.gettempdir(), f"project-{build_id}.tar.gz")
        remote_tar_path = f"/tmp/project-{build_id}.tar.gz"

        # 1. 打包本地项目
        self.logger(f"--> 正在将项目 '{local_project_path}' 打包到 '{local_tar_path}'...")
        try:
            with tarfile.open(local_tar_path, "w:gz") as tar:
                tar.add(local_project_path, arcname=os.path.basename(local_project_path))
            self.logger("--> 打包成功。")
        except Exception as e:
            self.logger(f"打包项目时出错: {e}")
            return False

        # 2. 上传项目压缩包
        if not self.upload_file(local_tar_path, remote_tar_path):
            self.logger("--> 上传失败，终止构建。")
            os.remove(local_tar_path) # 清理本地临时文件
            return False
        
        # 清理本地临时文件
        os.remove(local_tar_path)
        self.logger(f"--> 本地临时文件 '{local_tar_path}' 已清理。")

        # 3. 远程解压
        if self.execute_command(f"mkdir -p {remote_project_dir} && tar -xzf {remote_tar_path} -C {remote_project_dir}") != 0:
            self.logger("--> 远程解压失败，终止构建。")
            self.execute_command(f"rm -rf {remote_project_dir} {remote_tar_path}") # 清理
            return False

        # 4. 预检：检查远程服务器到私有仓库的连接
        self.logger("--> [诊断] 开始对远程环境进行预检...")
        check_command = f"curl -s --head --connect-timeout 10 https://{PRIVATE_REGISTRY}/v2/"
        self.logger(f"--> [诊断] 预检命令: {check_command}")
        if self.execute_command(check_command) != 0:
            self.logger("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            self.logger("!!! 预检失败: 远程服务器无法访问您的私有仓库 !!!")
            self.logger("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            self.logger(f"无法通过 'https://{PRIVATE_REGISTRY}/v2/' 连接到仓库。")
            self.logger("这通常是导致 Docker 拉取镜像时无限'Retrying'的根本原因。")
            self.logger("请在您的 VPS 上手动执行以下命令进行排查:")
            self.logger(f"    curl -v https://{PRIVATE_REGISTRY}/v2/")
            self.logger("常见原因及解决方案:")
            self.logger("  1. Caddy 或 Registry 容器未运行: `docker ps -a` 查看状态。")
            self.logger("  2. DNS 问题: 确保域名正确解析。在 VPS 上 `ping {PRIVATE_REGISTRY}`。")
            self.logger("  3. 防火墙问题: 检查 VPS 防火墙 (如 ufw) 是否允许 443 端口。")
            self.logger("  4. Docker 网络问题: 如果 Registry 容器在 Docker 网络中，确保 Docker daemon 可以访问它。")
            self.execute_command(f"rm -rf {remote_project_dir} {remote_tar_path}") # Cleanup
            return False
        self.logger("--> [诊断] 预检成功: 私有仓库连接正常。")

        # 5. 远程登录到私有仓库
        if not REGISTRY_USER or not REGISTRY_PASS:
            self.logger("--> 警告: 未在 config.ini 中配置 registry_user 或 registry_pass，跳过远程登录。")
        else:
            login_command = f"echo '{REGISTRY_PASS}' | docker login {PRIVATE_REGISTRY} -u {REGISTRY_USER} --password-stdin"
            if self.execute_command(login_command) != 0:
                self.logger("--> 远程 Docker 登录失败，终止构建。")
                self.execute_command(f"rm -rf {remote_project_dir} {remote_tar_path}") # 清理
                return False

        # 6. 远程构建
        full_image_tag = f"{PRIVATE_REGISTRY}/{image_tag}"
        project_folder_name = os.path.basename(local_project_path.rstrip('/\\'))
        build_context_path = posixpath.join(remote_project_dir, project_folder_name)
        self.logger(f"--> [诊断] 本地项目文件夹名: {project_folder_name}")
        self.logger(f"--> [诊断] 远程构建上下文路径: {build_context_path}")
        build_command = f"docker build -t {full_image_tag} {build_context_path}"
        if self.execute_command(build_command) != 0:
            self.logger("--> 远程 Docker 构建失败，终止构建。")
            self.execute_command(f"rm -rf {remote_project_dir} {remote_tar_path}") # 清理
            return False

        # 6. 远程推送
        if self.execute_command(f"docker push {full_image_tag}") != 0:
            self.logger("--> 远程 Docker 推送失败。")
            # 即使推送失败，也继续清理
        else:
            self.logger(f"--> 镜像 '{full_image_tag}' 已成功推送！")

        # 7. 远程清理
        self.logger("--> 开始远程清理...")
        self.execute_command(f"docker rmi {full_image_tag}")
        self.execute_command(f"docker logout {PRIVATE_REGISTRY}")
        self.execute_command(f"rm -rf {remote_project_dir} {remote_tar_path}")
        self.logger("--> 远程清理完成。")

        return True