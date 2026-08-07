# 用自己的域名做图床（具名隧道）

快速隧道（`tunnel --url`）能跑通，但它**每次重启都换一个随机域名**。Hub 有自愈
机制兜着，可代价是：换名那一刻，所有已发出卡片里的图**当场全裂**，而且腾讯是
「用户滑到消息时才抓图」，所以裂的是历史消息，补发新卡也救不回来。

有自己的域名就没这个问题——域名固定，`image_host_base_url` 一次填死。

---

## 先说清楚：不需要 cert.pem

这是最容易踩的坑，报错长这样：

```
ERR Cannot determine default origin certificate path.
    No file cert.pem in [~/.cloudflared ...]
```

三种跑法的区别：

| 方式 | 需要 cert.pem | 域名 | 说明 |
| --- | --- | --- | --- |
| `tunnel --url http://...` | 否 | **随机，重启即变** | 快速隧道 |
| `tunnel run <名字>` | **是** | 固定 | 本地管理，需 `tunnel login` 授权 |
| `tunnel run --token eyJ...` | **否** | 固定 | **面板托管，推荐** |

官方原文：*A remotely-managed tunnel only requires a token to run.*

`cert.pem` 是**管理**隧道（在命令行创建/删除）用的凭证，不是**运行**隧道用的。
在面板上建好隧道、把 token 交给容器，就完全绕开了浏览器授权这一步。

---

## 步骤

### 1. 把域名托管到 Cloudflare

在域名注册商那里，把 NS（nameserver）改成 Cloudflare 给的两个地址。

- Cloudflare 面板 → Add a site → 输入 `你的域名` → 选 Free 套餐
- 它会给两个形如 `xxx.ns.cloudflare.com` 的地址
- 回注册商后台替换原有 NS

生效通常几分钟到几小时。**Cloudflare 面板显示 Active 之后再往下做**，否则隧道
建得出来但域名解析不过去。

### 2. 在面板建隧道

Cloudflare 面板 → **Zero Trust** → **Networks** → **Tunnels** → Create a tunnel
→ 选 **Cloudflared** → 起个名字（如 `astrbot`）。

建完会给一条安装命令，里面 `eyJ...` 那一长串就是 **token**。只抄 token。

> ⚠️ token 等价于「谁拿到谁就能把流量接进你的域名」。别贴进聊天记录、issue、
> 截图。泄漏了就在面板上删掉隧道重建。

### 3. 配 Public Hostname

同一页往下，**Public Hostnames** → Add：

| 字段 | 填什么 |
| --- | --- |
| Subdomain | `img`（随便，只要不和现有记录冲突） |
| Domain | 你的域名 |
| Type | `HTTP` |
| URL | `127.0.0.1:9527` |

于是 `https://img.你的域名` → Hub 图床的 9527 端口。

`Type` 填 **HTTP 而不是 HTTPS**：公网到 Cloudflare 那一段是 HTTPS，
Cloudflare 到图床这一段走的是容器内 loopback，图床本身不监听 TLS。
填 HTTPS 会得到 502。

### 4. 换掉 cloudflared 容器

```bash
docker rm -f cloudflared

docker run -d --name cloudflared --restart unless-stopped \
  --network container:astrbot \
  cloudflare/cloudflared:latest \
  tunnel --no-autoupdate run --token eyJ你的token
```

`--network container:astrbot` 让它和 AstrBot **共用网络栈**，所以
`127.0.0.1:9527` 直接就是图床，不用再折腾容器间互访。

> 用 `--network container:` 时不能再加 `-p`，端口由被共享的那个容器决定。

### 5. 填 Hub 配置

WebUI → 插件配置 → `image_host_base_url` 填 `https://img.你的域名`
（**不要带结尾斜杠**），重载插件。

填了值之后 Hub 就认为这是**你对自己基础设施的承诺**，不再去问 cloudflared，
也永远不会覆盖它。自动发现只服务于「留空 = 用快速隧道」那条路。

### 6. 验证

```
@bot /诊断        → 图床 就绪，└ https://img.你的域名（固定）
@bot /图床测试     → 看到彩色棋盘格
@bot /诊断        → 「被抓取 N 次」应当 ≥1
```

「固定」两个字是关键：显示「自动发现」说明 `base_url` 没填上，还在用快速隧道。

---

## 排障

| 现象 | 原因 |
| --- | --- |
| 诊断显示「缺 base_url」 | 配置没填，或填了没重载插件 |
| 卡片裂图、诊断却「就绪」 | Public Hostname 没配，或 Type 填成了 HTTPS |
| 502 Bad Gateway | URL 写错端口，或没用 `--network container:astrbot` |
| 1033 / 隧道错误页 | cloudflared 没连上，`docker logs cloudflared` 看 |
| 域名打不开但隧道正常 | NS 还没生效，等 Cloudflare 显示 Active |
| 又出现 cert.pem 报错 | 命令里漏了 `--token`，退化成本地管理模式了 |

---

## 顺带：要不要给图床加访问控制？

**不要。** 腾讯的抓图服务器不会带任何凭证，加了 Cloudflare Access、
WAF 挑战、Bot Fight Mode，结果都是它抓不到图。

图床的安全性靠的是另外三件事，与鉴权无关：

* 只服务自己写进去的字节，文件名是 18 字节随机 token，猜不到；
* 只接受 GET，没有任何写接口；
* 图片几分钟内自动删除。

也就是说，能看到图的前提是**已经拿到了完整 URL**——而 URL 只出现在那张卡片里。
