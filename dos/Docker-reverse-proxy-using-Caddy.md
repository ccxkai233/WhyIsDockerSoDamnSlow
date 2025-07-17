# 云端 Docker Registry 服务架构说明

本文档旨在详细说明部署在 VPS 上的 Docker Registry 服务栈的当前架构、组件功能及数据流。

## 1. 核心架构

本服务栈采用**双实例**模式，通过 Caddy 作为智能反向代理，将一个域名下的流量根据功能需求分发到两个独立的 Docker Registry 容器。这种架构实现了**私有仓库**和**公共镜像拉取缓存**的功能分离，确保了系统的清晰、安全与高效。

## 2. 组件概览

服务由 3 个核心 Docker 容器组成，通过 `docker-compose` 进行编排：

| 服务名称 (`docker-compose.yml`) | 容器镜像 | 核心功能 | 数据存储路径 (在 VPS 上) |
| :--- | :--- | :--- | :--- |
| `registry` | `registry:2` | **私有仓库** (可读可写) | `./registry/data/` |
| `registry-cache` | `registry:2` | **拉取缓存** (只读) | `./registry-cache/data/` |
| `caddy` | `caddy:2-alpine` | **智能反向代理** / **TLS 证书自动管理** | `./caddy/data/` |

## 3. 域名与流量分发

Caddy 服务器监听 80 和 443 端口，并根据访问的域名将流量转发到对应的后端服务：

### 3.1. `dcr.your-domain.com` - 私有仓库

- **功能**: 用于存储您自己构建的私有 Docker 镜像。
- **权限**: 可读、可写 (需要通过 `docker login` 认证)。
- **流量路径**:
  ```mermaid
  graph TD
      A[开发者/CI/CD] -- docker push/pull --> B(dcr.your-domain.com);
      B -- HTTPS/443 --> C[Caddy 服务器];
      C -- 内部网络 --> D[registry 容器];
      D -- 读/写 --> E[./registry/data/];
  ```
- **使用场景**:
  - `docker push dcr.your-domain.com/my-app:1.0`
  - `docker pull dcr.your-domain.com/my-app:1.0`

### 3.2. `mir.your-domain.com` - 拉取缓存

- **功能**: 作为 Docker Hub 的一个只读缓存。当您第一次通过此域名拉取公共镜像时，它会从 Docker Hub 下载并存储在自己的空间；后续再次拉取同一个镜像时，将直接从缓存中高速获取。
- **权限**: 只读 (匿名可拉取，但拒绝任何 `push` 操作)。
- **流量路径**:
  ```mermaid
  graph TD
      A[开发者/CI/CD] -- docker pull --> B(mir.your-domain.com);
      B -- HTTPS/443 --> C[Caddy 服务器];
      C -- 内部网络 --> D[registry-cache 容器];
      D -- 检查缓存 --> E{缓存命中?};
      E -- 是 --> F[./registry-cache/data/];
      F --> A;
      E -- 否 --> G[Docker Hub];
      G -- 下载镜像 --> D;
      D -- 写入缓存 --> F;
  ```
- **使用场景**:
  - `docker pull mir.your-domain.com/library/ubuntu:22.04`
  - 在需要加速的机器上配置 Docker daemon 的 `"registry-mirrors"`。

## 4. 配置文件核心摘要

- **`docker-compose.yml`**: 定义了 `registry`, `registry-cache`, `caddy` 三个服务，并将它们连接在同一个 `proxy_net` 网络中，使得 Caddy 可以通过服务名访问到两个 Registry 实例。
- **`registry/config.yml`**: 标准的私有仓库配置，启用了 `htpasswd` 认证。
- **`registry-cache/config.yml`**: 核心是 `proxy` 配置块，它指向 `https://registry-1.docker.io`，这是实现拉取缓存的关键。由于没有 `auth` 块，它默认是只读的。
- **`caddy/Caddyfile`**: 定义了两个独立的域名块 (`dcr.your-domain.com` 和 `mir.your-domain.com`)，分别使用 `reverse_proxy` 指令将流量转发到 `registry:5000` 和 `registry-cache:5000`。Caddy 会自动为这两个域名处理 Let's Encrypt 的 TLS 证书申请和续期。

## 5. 总结

当前架构稳定、清晰且功能分离。`dcr` 域名专注于私有资产的安全存储，`mir` 域名专注于公共依赖的拉取加速。两者互不干扰，为后续的 CI/CD 集成和开发者协作打下了坚实的基础。

## 6. 运维与管理

### 6.1. 自动定期清理缓存

标准的 Docker Registry 作为拉取缓存时，本身没有内置的自动过期机制。为了防止缓存无限增长占满磁盘，建议设置一个定时任务来定期清理。

最合适的方法是使用 **cron 定时任务**来执行一个清理脚本。

#### 第 1 步：创建清理脚本

在 VPS 上创建一个脚本文件，例如 `/home/user/scripts/cleanup_registry_cache.sh`。

```bash
#!/bin/bash

# 设置日志文件路径
LOG_FILE="/var/log/registry_cleanup.log"

# 切换到 docker-compose 项目目录
cd /srv/registry-stack || { echo "错误：无法进入 /srv/registry-stack 目录" >> $LOG_FILE; exit 1; }

echo "=========================================" >> $LOG_FILE
echo "开始执行缓存清理任务：$(date)" >> $LOG_FILE
echo "-----------------------------------------" >> $LOG_FILE

# 1. 对缓存仓库执行垃圾回收 (Garbage Collection)
#    这会删除所有不再被任何镜像引用的数据层 (blobs)
echo "步骤 1: 对 registry-cache 执行垃圾回收..." >> $LOG_FILE
docker compose exec registry-cache bin/registry garbage-collect /etc/docker/registry/config.yml >> $LOG_FILE 2>&1
echo "垃圾回收完成。" >> $LOG_FILE
echo "-----------------------------------------" >> $LOG_FILE

# 2. 清理超过 30 天未被访问过的旧缓存文件
#    这能确保缓存不会无限增长，只保留近期常用的镜像
echo "步骤 2: 删除超过 30 天未使用的缓存文件..." >> $LOG_FILE
# 注意：这里的路径是 VPS 上的实际数据卷路径
CACHE_DATA_DIR="/srv/registry-stack/registry-cache/data/docker/registry/v2/blobs"
find "$CACHE_DATA_DIR" -type f -atime +30 -delete -print >> $LOG_FILE
echo "旧文件清理完成。" >> $LOG_FILE
echo "-----------------------------------------" >> $LOG_FILE

# 3. 检查磁盘空间 (可选，但推荐)
echo "步骤 3: 检查当前磁盘使用情况..." >> $LOG_FILE
df -h /srv/registry-stack/ >> $LOG_FILE
echo "=========================================" >> $LOG_FILE
echo "" >> $LOG_FILE
```

创建后，需要赋予其可执行权限：
```bash
chmod +x /home/user/scripts/cleanup_registry_cache.sh
```

#### 第 2 步：设置 cron 定时任务

执行 `crontab -e` 命令，在文件末尾添加一行，设置一个定时任务（例如每周日凌晨3点执行）：

```crontab
0 3 * * 0 /bin/bash /home/user/scripts/cleanup_registry_cache.sh
```
*(请将 `/home/user` 替换为实际的用户 home 目录)*

### 6.2. 手动清理私有仓库

与缓存仓库不同，私有仓库的数据通常不建议自动删除。当需要回收空间时，可以手动执行垃圾回收。

1.  **删除镜像**: 首先，需要通过 [Registry API](https://docs.docker.com/registry/spec/api/) 删除不再需要的镜像标签或整个仓库。
2.  **执行垃圾回收**: 删除完成后，在 VPS 上执行以下命令来清理未被引用的数据层：
    ```bash
    docker compose exec registry bin/registry garbage-collect /etc/docker/registry/config.yml
    ```