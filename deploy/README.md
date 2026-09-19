# AI回忆录（AIMemory）官网 · 部署指南

整站为纯静态文件（HTML/CSS/JS），无构建步骤。两种上线方式，任选其一。

---

## 方式 A：阿里云 ECS / 轻量服务器 + Nginx（推荐，灵活）

### 1. 准备服务器
- 系统：Ubuntu / CentOS，已安装 `nginx`、`rsync`、`openssh-server`
- 开放安全组：80、443（入站）

### 2. 本地一键部署
```bash
cd deploy
REMOTE_HOST=你的服务器公网IP \
REMOTE_USER=root \
REMOTE_DIR=/var/www/aimemory \
./deploy.sh
```
脚本会把整站同步到服务器 `/var/www/aimemory`，并推送 Nginx 配置、重载 Nginx。

### 3. 证书（二选一）
- **Let's Encrypt**：服务器上 `certbot --nginx -d aimemory.cafe -d www.aimemory.cafe`
- **阿里云证书**：下载 Nginx 版证书，按 `nginx-aimemory.cafe.conf` 注释改 `ssl_certificate` 路径

### 4. DNS
阿里云 DNS 控制台把 `aimemory.cafe` 与 `www.aimemory.cafe` 的 A 记录指向服务器公网 IP。

---

## 方式 B：阿里云 OSS 静态托管 + CDN（最省心，免运维）

1. 建 Bucket（地域选**中国大陆**，读写权限「公共读」，开通「静态网站托管」，默认首页 `index.html`）
2. 上传整站：
   ```bash
   ossutil cp -r ../ oss://你的bucket/ --exclude '.workbuddy/*' --exclude 'deploy/*'
   ```
3. 绑定自定义域名 `aimemory.cafe`（OSS 控制台 → 传输管理 → 域名管理），上传阿里云证书开启 HTTPS
4. 可选：套 CDN 加速，回源类型「源站域名」填 Bucket 访问域名
5. DNS 把 `aimemory.cafe` CNAME 到 OSS/CDN 提供的域名

---

## 备案号（已填）
页脚备案号：`京ICP备2026024457号-2`（已加 beian.miit.gov.cn 工信部查询链接）。如需变更，执行：
```bash
./replace_icp.sh 新的备案号   # 例如 ./replace_icp.sh 京ICP备XXXXXX号-1
```

## 联系方式（已填）
- 邮箱：sale@aimemory.cafe
- 电话 / 微信：13321118307
- 版权主体：山西金润瀚宇科技有限公司

## 目录结构
```
index.html        首页
product.html      产品展示
apps.html         相关应用（App/网页版/我的好爸爸/山西金润瀚宇科技有限公司）
agreement.html    用户协议
privacy.html      隐私政策
contact.html      联系我们
assets/           样式、脚本、图标
deploy/           本目录（部署配置与脚本）
```
