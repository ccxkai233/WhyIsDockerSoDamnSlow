import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
from ttkthemes import ThemedTk
import threading
import os
import subprocess
import tarfile
import tempfile
import configparser

from .config import PRIVATE_REGISTRY, CACHE_REGISTRY
from .docker_helpers import transform_image_name, accelerate_command, get_image_name_from_input, parse_dockerfile, accelerate_dockerfile_content
from .ssh_manager import SSHManager

class App(ThemedTk):
    STATE_FILE = "build_state.ini"

    def __init__(self):
        super().__init__(theme="arc")

        self.title("Docker 加速与远程构建工具")
        self.geometry("750x750")

        # --- 样式 ---
        style = ttk.Style(self)
        # 定义通用字体
        default_font = ("Segoe UI", 10)
        entry_font = ("Consolas", 11)
        label_font = ("Segoe UI", 11, "bold")

        style.configure("TLabel", font=default_font)
        style.configure("TButton", font=default_font)
        style.configure("TEntry", font=entry_font)
        style.configure("TLabelframe.Label", font=label_font)

        # --- 布局 ---
        self.columnconfigure(0, weight=1)
        
        # --- 创建组件 ---
        self._create_conversion_widgets()
        self._create_build_widgets()
        self._create_dockerfile_widgets()
        self._create_log_and_action_widgets()
        
        self.grid_rowconfigure(3, weight=1)
        self.input_entry.bind("<Return>", self.convert)
        
        self._load_state()

    def _create_conversion_widgets(self):
        frame = ttk.LabelFrame(self, text="命令转换 (用于拉取或预热)", padding="10")
        frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="原始命令:").grid(row=0, column=0, padx=(0, 5), sticky="w")
        self.input_var = tk.StringVar()
        self.input_entry = ttk.Entry(frame, textvariable=self.input_var)
        self.input_entry.grid(row=0, column=1, sticky="ew")

        ttk.Label(frame, text="加速命令:").grid(row=1, column=0, padx=(0, 5), sticky="w")
        self.output_var = tk.StringVar()
        self.output_entry = ttk.Entry(frame, textvariable=self.output_var, state="readonly")
        self.output_entry.grid(row=1, column=1, sticky="ew", pady=5)
        
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=2, column=0, columnspan=2, sticky="e", pady=(5, 0))
        
        self.convert_button = ttk.Button(button_frame, text="转换", command=self.convert)
        self.convert_button.pack(side="left", padx=(0, 5))
        self.copy_button = ttk.Button(button_frame, text="复制", command=self.copy_to_clipboard)
        self.copy_button.pack(side="left", padx=(0, 5))
        self.preheat_button = ttk.Button(button_frame, text="预热镜像", command=self.start_preheat_thread)
        self.preheat_button.pack(side="left")

    def _create_build_widgets(self):
        """创建远程构建并推送的组件"""
        frame = ttk.LabelFrame(self, text="远程构建并推送", padding="10")
        frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        frame.columnconfigure(1, weight=1)

        # 选择项目目录
        ttk.Label(frame, text="项目目录:").grid(row=0, column=0, padx=(0, 5), sticky="w")
        self.project_dir_var = tk.StringVar()
        self.project_dir_entry = ttk.Entry(frame, textvariable=self.project_dir_var, state="readonly")
        self.project_dir_entry.grid(row=0, column=1, sticky="ew")
        self.browse_button = ttk.Button(frame, text="浏览...", command=self.browse_project_directory)
        self.browse_button.grid(row=0, column=2, padx=5)

        # 镜像名称
        ttk.Label(frame, text="镜像标签:").grid(row=1, column=0, padx=(0, 5), sticky="w")
        self.image_tag_var = tk.StringVar()
        self.image_tag_entry = ttk.Entry(frame, textvariable=self.image_tag_var)
        self.image_tag_entry.grid(row=1, column=1, columnspan=2, sticky="ew", pady=5)
        # self.image_tag_entry.insert(0, "your-app-name:latest") # Replaced by _load_state

        self.build_button = ttk.Button(frame, text="开始构建并推送", command=self.start_build_and_push_thread)
        self.build_button.grid(row=2, column=1, columnspan=2, sticky="e", pady=(10, 0))

    def _create_dockerfile_widgets(self):
        """创建 Dockerfile 批量预热的组件"""
        frame = ttk.LabelFrame(self, text="Dockerfile 批量预热", padding="10")
        frame.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
        frame.columnconfigure(0, weight=1)

        self.dockerfile_preheat_button = ttk.Button(frame, text="选择 Dockerfile 并预热所有基础镜像", command=self.start_dockerfile_preheat_thread)
        self.dockerfile_preheat_button.grid(row=0, column=0, sticky="ew", ipady=5)

    def _create_log_and_action_widgets(self):
        frame = ttk.LabelFrame(self, text="远程操作日志", padding="10")
        frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=5)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        
        self.log_text = scrolledtext.ScrolledText(frame, wrap=tk.WORD, font=("Consolas", 9), state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        # 进度条
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=1, column=0, sticky="ew", pady=5)
        
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=2, column=0, sticky="e", pady=5)

    def _load_state(self):
        """加载上次的构建状态"""
        try:
            if os.path.exists(self.STATE_FILE):
                config = configparser.ConfigParser()
                config.read(self.STATE_FILE, encoding='utf-8')
                last_tag = config.get('Build', 'last_image_tag', fallback='your-app-name:latest')
                self.image_tag_var.set(last_tag)
            else:
                self.image_tag_var.set("your-app-name:latest")
        except Exception as e:
            print(f"无法加载状态: {e}")
            self.image_tag_var.set("your-app-name:latest")

    def _save_state(self):
        """保存当前的构建状态"""
        try:
            config = configparser.ConfigParser()
            config['Build'] = {'last_image_tag': self.image_tag_var.get()}
            with open(self.STATE_FILE, 'w', encoding='utf-8') as configfile:
                config.write(configfile)
        except Exception as e:
            print(f"无法保存状态: {e}")

    def browse_project_directory(self):
        """打开文件对话框以选择项目目录。"""
        directory = filedialog.askdirectory(title="选择项目根目录 (必须包含 Dockerfile)")
        if directory:
            self.project_dir_var.set(directory)

    def log(self, message):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")
        self.update_idletasks()

    def update_progress(self, sent, total):
        """更新进度条的回调函数"""
        if total > 0:
            progress = (sent / total) * 100
            self.progress_var.set(progress)
        self.update_idletasks()

    def convert(self, event=None):
        original_command = self.input_var.get()
        if original_command:
            new_command = accelerate_command(original_command)
            self.output_var.set(new_command)

    def copy_to_clipboard(self):
        new_command = self.output_var.get()
        if new_command:
            self.clipboard_clear()
            self.clipboard_append(new_command)
            self.update()

    def _set_buttons_state(self, state):
        self.preheat_button.config(state=state)
        self.dockerfile_preheat_button.config(state=state)
        self.build_button.config(state=state)

    def _start_thread(self, target_func, *args):
        self._set_buttons_state("disabled")
        self.log_text.config(state="normal")
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state="disabled")
        
        import threading
        thread = threading.Thread(target=target_func, args=args, daemon=True)
        thread.start()

    def start_preheat_thread(self):
        image_name = get_image_name_from_input(self.input_var.get())
        if not image_name:
            self.log("错误: 请先在“原始命令”框中输入要预热的镜像。")
            return
        self._start_thread(self.preheat_images, [image_name], None)

    def start_dockerfile_preheat_thread(self):
        filepath = filedialog.askopenfilename(
            title="选择 Dockerfile",
            filetypes=(("Dockerfile", "Dockerfile*"), ("所有文件", "*.*"))
        )
        if not filepath:
            return

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            images = parse_dockerfile(content)
            if not images:
                self.log("错误: 在选择的文件中没有找到任何 FROM 指令。")
                return
            
            self._start_thread(self.preheat_images, images, content)

        except Exception as e:
            self.log(f"读取或解析 Dockerfile 时出错: {e}")

    def start_build_and_push_thread(self):
        project_dir = self.project_dir_var.get()
        image_tag = self.image_tag_var.get()

        if not project_dir or not image_tag:
            self.log("错误: 请先选择项目目录并指定镜像标签。")
            return
        
        if not os.path.exists(os.path.join(project_dir, "Dockerfile")):
            self.log(f"错误: 在 '{project_dir}' 中未找到 Dockerfile。")
            return

        self._start_thread(self.build_and_push, project_dir, image_tag)

    def build_and_push(self, project_dir, image_tag):
        """
        执行远程构建和推送的完整流程。
        """
        self.log(f"--- 开始远程构建项目: {project_dir} ---")
        self.log(f"--- 目标镜像: {PRIVATE_REGISTRY}/{image_tag} ---")
        manager = SSHManager(logger_func=self.log)

        try:
            if not manager.connect():
                self.log("错误: 无法连接到远程服务器。")
                return

            success = manager.build_and_push_project(project_dir, image_tag)

            if success:
                self.log("\n--- 远程构建并推送流程成功完成！ ---")
                self._save_state() # 保存状态
                full_image_tag = f"{PRIVATE_REGISTRY}/{image_tag}"
                pull_command = f"docker pull {full_image_tag}"
                self.log(f"镜像已推送到私有仓库。您现在可以在本地使用以下命令拉取：")
                self.log(f"--> {pull_command}")
                self.output_var.set(pull_command)
            else:
                self.log("\n--- 远程构建并推送流程失败。请检查以上日志。 ---")

        finally:
            manager.close()
            self._set_buttons_state("normal")

    def preheat_images(self, image_list, dockerfile_content=None):
        """
        接收一个镜像列表，并逐一进行预热。
        如果提供了 dockerfile_content，则在完成后生成加速后的构建命令。
        """
        self.log(f"--- 开始批量预热，共 {len(image_list)} 个镜像 ---")
        manager = SSHManager(logger_func=self.log)
        all_success = True
        
        try:
            if not manager.connect():
                self.log("错误: 无法连接到远程服务器。")
                return

            for i, image_name in enumerate(image_list):
                self.log(f"\n[{i+1}/{len(image_list)}] 正在预热: {image_name}")
                if '/' not in image_name:
                    normalized_image = f"library/{image_name}"
                else:
                    normalized_image = image_name
                
                cache_image_name = f"{CACHE_REGISTRY}/{normalized_image}"
                
                if manager.execute_command(f"docker pull {cache_image_name}") != 0:
                    self.log(f"!!! {image_name} 预热失败。")
                    all_success = False
                    continue

                manager.execute_command(f"docker rmi {cache_image_name}")
                self.log(f"--> {image_name} 预热完成。")
            
            if all_success:
                self.log("\n--- 所有镜像已成功预热！---")
                if dockerfile_content:
                    self.log("--> 正在生成管道模式的加速构建命令...")
                    accelerated_content = accelerate_dockerfile_content(dockerfile_content)
                    
                    # 为不同操作系统准备命令
                    # 对于 PowerShell 和 Linux/macOS (bash/zsh), echo -e "..." | ... 是可行的
                    # 但需要处理好引号转义
                    # 将内容中的双引号转义，以便可以被包含在 "..." 中
                    escaped_content = accelerated_content.replace('"', '\\"')
                    
                    # 使用换行符连接，并为 echo -e 准备
                    echo_content = '\\n'.join(escaped_content.splitlines())

                    # 生成最终命令
                    # 注意：在Windows的CMD中，这个命令可能无法直接工作，但在Git Bash或PowerShell中可以
                    build_command = f'echo -e "{echo_content}" | docker build -f - .'
                    
                    self.output_var.set(build_command)
                    self.log("--> 管道模式的加速构建命令已生成在“加速命令”框中。")
                    self.log("--> 请注意：此命令在 Linux, macOS, Git Bash, WSL 或 PowerShell 中效果最佳。")
            else:
                self.log("\n--- 部分镜像预热失败，请检查日志。---")

        finally:
            manager.close()
            self._set_buttons_state("normal")