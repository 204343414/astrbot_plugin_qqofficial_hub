# 用自己的域名做图床（具名隧道）

快速隧道（`tunnel --url`）能跑通，但它有两种独立的死法，实测都遇到过：

* **重启换名**——旧卡片里的图当场全裂，而腾讯是「用户滑到消息才抓图」，补发新卡也救不回历史消息；
* **域名直接失效**——进程还 `Up`，DNS 已经 `NXDOMAIN`。快速隧道的注册是有生命周期的。

有自己的域名，这两件事都不会再发生。

> **不需要公网 IP，也不需要 VPS。** cloudflared 是从你的机器**主动向外**建连接，
> Cloudflare 顺着这条连接把流量送回来。家宽、NAT、没有备案，都不影响。

---

## 先搞清楚 cert.pem 到底是什么

这是最容易卡住的地方，报错长这样：

```
ERR Cannot determine default origin certificate path.
    No file cert.pem in [~/.cloudflared ...]
```

看官方定义：

> **cert.pem**：运行 `cloudflared tunnel login` 时由 Cloudflare 签发。
> 在**创建隧道、删除隧道、改 DNS 记录、从 cloudflared 配置路由**时需要它。
> **运行一个已存在的隧道、或从面板管理路由时，不需要这个文件。**

也就是说 `cert.pem` 是**管理**凭证，不是**运行**凭证。之前那个报错的原因是：
跑了 `tunnel run <名字>` 却从没 `tunnel login` 过——命令想去查「这个名字对应哪个隧道」，
而查询需要管理凭证。

由此有两条路，**选一条走完就行**：

| | 方案 A：面板托管 | 方案 B：命令行 |
| --- | --- | --- |
| 需要 cert.pem | 否 | 是（`tunnel login` 自动生成） |
| 需要浏览器授权 | 否 | 是（一次） |
| 配置改在哪 | 网页面板 | 本机 yaml |
| 可能的卡点 | Zero Trust 开通时**可能要求绑支付方式** | Docker 里要挂载凭证目录 |

**先试方案 A。如果它要你绑卡而你不想绑，转方案 B**——B 只用普通 Cloudflare 账号，
不碰 Zero Trust，不会问支付方式。

---

# 方案 A：面板托管（推荐先试）

## A1. 建隧道

Cloudflare 面板 → **Zero Trust** → **Networks** → **Tunnels** → Create a tunnel
→ 选 **Cloudflared** → 命名（如 `astrbot`）。

> 如果这一步要求填信用卡：Zero Trust 免费版在部分地区/时期会强制绑定支付方式。
> 不想绑就直接跳到**方案 B**，功能完全一样。

建完给你一条安装命令，里面 `eyJ...` 那一长串就是 **token**，只抄它。

> ⚠️ **token 谁拿到谁就能把流量接进你的域名。** 别贴进聊天、issue、截图。
> 泄漏了就在面板删掉隧道重建。

## A2. 配 Public Hostname

同一页往下 → **Public Hostnames** → Add：

| 字段 | 填什么 |
| --- | --- |
| Subdomain | `img` |
| Domain | `8700k.top` |
| Path | 留空 |
| Type | **HTTP** |
| URL | `127.0.0.1:9527` |

**Type 必须是 HTTP 不是 HTTPS**：公网到 Cloudflare 那段本来就是 HTTPS，
Cloudflare 到图床这段走容器内 loopback，图床自己不监听 TLS。填 HTTPS 会得到 502。

DNS 记录面板会自动建，不用手动加。

## A3. 换掉 cloudflared 容器

```bash
docker rm -f cloudflared

docker run -d --name cloudflared --restart unless-stopped \
  --network container:astrbot \
  cloudflare/cloudflared:latest \
  tunnel --no-autoupdate run --token eyJ你的token
```

跳到**第三部分：填配置**。

---

# 方案 B：命令行（不碰 Zero Trust）

只需要普通 Cloudflare 账号。三条命令，凭证存在一个 Docker volume 里。

## B1. 登录（一次性，需要浏览器）

```bash
docker run -it --rm -v cfcreds:/home/nonroot/.cloudflared \
  cloudflare/cloudflared:latest tunnel login
```

它会打印一条 URL。**复制到你已登录 Cloudflare 的浏览器**打开，
选 `8700k.top`，点 Authorize。终端出现 `You have successfully logged in` 即可。

> 这一步生成的就是 `cert.pem`，存进了 `cfcreds` 这个 volume。
> 它是**账号级**凭证，能管理你账号下所有隧道——所以只放在自己机器上。

## B2. 建隧道 + 绑域名

```bash
docker run -it --rm -v cfcreds:/home/nonroot/.cloudflared \
  cloudflare/cloudflared:latest tunnel create astrbot

docker run -it --rm -v cfcreds:/home/nonroot/.cloudflared \
  cloudflare/cloudflared:latest tunnel route dns astrbot img.8700k.top
```

第二条会自动在 DNS 里建一条 CNAME，不用手动加。

## B3. 写配置文件

```bash
docker run -it --rm -v cfcreds:/home/nonroot/.cloudflared \
  --entrypoint sh cloudflare/cloudflared:latest -c '
UUID=$(ls /home/nonroot/.cloudflared/*.json | head -1 | xargs basename | sed s/.json//)
cat > /home/nonroot/.cloudflared/config.yml <<EOF
tunnel: $UUID
credentials-file: /home/nonroot/.cloudflared/$UUID.json
ingress:
  - hostname: img.8700k.top
    service: http://127.0.0.1:9527
  - service: http_status:404
EOF
echo "--- 写入完成 ---"; cat /home/nonroot/.cloudflared/config.yml'
```

## B4. 跑起来

```bash
docker rm -f cloudflared

docker run -d --name cloudflared --restart unless-stopped \
  --network container:astrbot \
  -v cfcreds:/home/nonroot/.cloudflared \
  cloudflare/cloudflared:latest \
  tunnel --no-autoupdate run
```

---

# 第三部分：填配置（两条路共用）

AstrBot WebUI → 插件配置 → QQ Official Hub：

```
image_host_base_url = https://img.8700k.top
```

**不要带结尾斜杠**（带了也会自动去掉，但别依赖这个）。保存后重载插件。

填了值之后 Hub 就认为这是**你对自己基础设施的承诺**：不再去问 cloudflared，
也永远不会覆盖它。自动发现只服务于「留空 = 用快速隧道」那条路。

## 验证

```
@bot /诊断        → 图床 就绪，└ https://img.8700k.top（固定）
@bot /图床测试     → 看到彩色棋盘格
@bot /诊断        → 「被抓取 N 次」应当 ≥1
```

**「固定」两个字是关键**。显示「自动发现」说明 `base_url` 没生效，还在用快速隧道。

---

## 关于 `--network container:astrbot`

让 cloudflared 和 AstrBot **共用同一个网络栈**，所以 `127.0.0.1:9527` 直接就是图床，
不用配容器间互访、不用改图床监听地址。

代价是：用了这个就**不能再加 `-p`**，端口全由 astrbot 容器决定。
另外 **astrbot 容器重启后，cloudflared 必须跟着重启**——它的网络栈没了。

```bash
docker restart astrbot && docker restart cloudflared
```

---

## 排障

| 现象 | 原因 |
| --- | --- |
| 诊断显示「缺 base_url」 | 配置没填，或填了没重载插件 |
| 诊断显示「自动发现」而不是「固定」 | `image_host_base_url` 没生效 |
| 卡片裂图但诊断「就绪」 | Public Hostname 没配，或 Type 填成了 HTTPS |
| 502 Bad Gateway | 图床没在跑；或端口写错；或没用 `--network container:astrbot` |
| 1033 / 隧道错误页 | cloudflared 没连上，`docker logs cloudflared` 看 |
| 又出现 cert.pem 报错 | 方案 A 漏了 `--token`；方案 B 漏了挂载 `-v cfcreds:...` |
| `tunnel login` 打不开链接 | 复制到已登录 Cloudflare 的浏览器手动打开 |

---

## 顺带：为什么图床不能加访问控制

**不要**给 `img.8700k.top` 加 Cloudflare Access、WAF 挑战或 Bot Fight Mode。

腾讯的抓图服务器不带任何凭证，加了这些的结果都是**它抓不到图**，
而症状是「卡片裂图」——看起来像图床坏了。

图床的安全性来自另外三件事，与鉴权无关：

* 只服务自己写进去的字节，文件名是 18 字节随机 token，猜不到；
* 只接受 GET，没有任何写接口；
* 图片几分钟内自动删除。

能看到图的前提是**已经拿到完整 URL**——而 URL 只出现在那张卡片里。

---

## 那台 VPS 和 bot.8700k.top

隧道跑通、`/图床测试` 通过之后，那台 VPS 就可以不要了：

1. Cloudflare 面板 → DNS → 删掉 `bot` 那条 A 记录（当前指向 VPS）；
2. VPS 到期不续。

`img.8700k.top` 走隧道，跟 VPS 没有任何关系。域名留着就行。
