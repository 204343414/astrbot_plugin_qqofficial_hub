# 让快速隧道自己活过来（不用买域名）

快速隧道有两种死法，都实测遇到过：

1. **重启换名**——Hub 自己能发现新域名（v0.23.1 起每分钟探测一次），已解决；
2. **域名整个失效**——容器还 `Up`，DNS 已经 `NXDOMAIN`。这条 Hub 救不了，
   因为 cloudflared 不觉得自己有问题，**必须重启**它才会去申请新域名。

补上最后一环：发现隧道不通就重启 cloudflared。加上 Hub 的自动发现，全链路无人值守。

---

## 先说三个实测结论

**一、cloudflared 官方镜像是 distroless。** 没有 shell、没有 `wget`、没有 `curl`，
`docker exec` 进不去。所以

```bash
# ❌ 这样写健康检查跑不起来，镜像里根本没有 wget
--health-cmd 'wget -qO- http://127.0.0.1:20241/ready || exit 1'
```

网上大量教程这么写，照抄会得到一个永远 unhealthy 的容器。探测必须**从外部容器**发起。

**二、metrics 的两个端点都真实存在**（本地实测）：

```
GET /ready       → {"status":200,"readyConnections":1,"connectorId":"..."}
GET /quicktunnel → {"hostname":"xxx.trycloudflare.com"}
```

`/quicktunnel` 用 `text/plain` 返回 JSON，所以别用会检查 Content-Type 的解析器。

**三、502 和 000 是完全不同的故障，绝不能一起处理。**

| 公网探测返回 | 含义 | 该做什么 |
| --- | --- | --- |
| `200` | 一切正常 | 什么都不做 |
| `502/503/504` | **隧道活着**，只是图床没响应 | **不要重启隧道**——问题在 AstrBot 那边 |
| `000` | 连不上：域名失效或隧道断了 | 重启 cloudflared，换新域名 |

这个区分是实测出来的：我把隧道指向一个没有服务的端口，得到的是 502 而不是失败。
**如果把 502 也当成隧道故障，就会在图床本身有问题时反复重启隧道**——
每次换一个新域名，把「一个坏掉的图床」变成「一个坏掉且地址一直在变的图床」。

---

## 看门狗

一个共享 astrbot 网络栈的小容器，用 alpine（有 curl，也有 shell）。

```bash
mkdir -p ~/bin && cat > ~/bin/tunnel-watchdog.sh <<'EOF'
#!/bin/sh
# 快速隧道看门狗：只在隧道真的不可达时重启它。
#
# 为什么要从公网打而不是打本地：本地永远是通的。真正要回答的问题是
# 「腾讯能不能取到这张图」，那只有走完整条链路才测得出来。

METRICS="http://127.0.0.1:${METRICS_PORT:-20241}"
THRESHOLD="${THRESHOLD:-3}"        # 连续 3 次（约 3 分钟）才动手，避开抖动
FAILS=0

echo "watchdog 启动，metrics=$METRICS 阈值=$THRESHOLD"

while true; do
  sleep 60

  HOST=$(curl -s --max-time 5 "$METRICS/quicktunnel" \
         | sed -n 's/.*"hostname":"\([^"]*\)".*/\1/p')

  if [ -z "$HOST" ]; then
    FAILS=$((FAILS+1))
    echo "$(date '+%F %T') 拿不到隧道域名 ($FAILS/$THRESHOLD)"
  else
    CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "https://$HOST/healthz")
    case "$CODE" in
      200)
        [ "$FAILS" -gt 0 ] && echo "$(date '+%F %T') 恢复正常 $HOST"
        FAILS=0
        ;;
      502|503|504)
        # 隧道是通的，是图床没应答。重启隧道只会换个域名，
        # 把问题从「图床坏了」变成「图床坏了而且地址还在变」。
        echo "$(date '+%F %T') $HOST 返回 $CODE：隧道正常，图床没响应（不重启隧道）"
        FAILS=0
        ;;
      *)
        FAILS=$((FAILS+1))
        echo "$(date '+%F %T') $HOST 返回 $CODE ($FAILS/$THRESHOLD)"
        ;;
    esac
  fi

  if [ "$FAILS" -ge "$THRESHOLD" ]; then
    echo "$(date '+%F %T') 重启 cloudflared"
    docker restart cloudflared || echo "重启失败：docker.sock 挂载了吗？"
    FAILS=0
    sleep 120        # 等新隧道建好，别急着再判一次
  fi
done
EOF
chmod +x ~/bin/tunnel-watchdog.sh
```

跑起来（`docker:cli` 镜像自带 docker 客户端和 curl）：

```bash
docker run -d --name tunnel-watchdog --restart unless-stopped \
  --network container:astrbot \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v ~/bin/tunnel-watchdog.sh:/watchdog.sh:ro \
  docker:cli sh /watchdog.sh
```

看日志：

```bash
docker logs -f tunnel-watchdog
```

> **`docker.sock` 等于给了这个容器重启任何容器的权力。** 自己家里的机器无所谓，
> 多人服务器上要想清楚。

### 不想跑容器？用 systemd

脚本一样，把 `METRICS` 改成 `http://127.0.0.1:20241`（宿主机能直连的话）：

```bash
sudo tee /etc/systemd/system/tunnel-watchdog.service > /dev/null <<EOF
[Unit]
Description=Cloudflare quick tunnel watchdog
After=docker.service
Requires=docker.service

[Service]
ExecStart=$HOME/bin/tunnel-watchdog.sh
Restart=always
User=$USER

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now tunnel-watchdog
journalctl -u tunnel-watchdog -f
```

注意 metrics 要监听在容器外可达的地址（`--metrics 0.0.0.0:20241`），
否则宿主机上的脚本连不上。

---

## 全链路怎么闭环

```
隧道换域名   → Hub 每分钟探测 metrics，自动换 base_url        （v0.23.1）
隧道没起来   → Hub 后台守护持续重试，一出现就自动上线          （v0.23.1）
插件重载     → 新实例接管端口，继承图片和抓取计数              （v0.23.0）
域名失效/断连 → 看门狗公网探测连续失败，重启换新域名            （本文）
图床自己坏了 → 看门狗识别 502 并按兵不动，交给 Hub 的重试        （本文）
```

每一环都不需要人。

---

## astrbot 重启时别忘了

用 `--network container:astrbot` 时，cloudflared 和看门狗**共用 astrbot 的网络栈**。
astrbot 一重启，网络栈就没了，两个容器都会变成僵尸状态。

```bash
docker restart astrbot && docker restart cloudflared tunnel-watchdog
```

写成 alias 省得记：

```bash
echo "alias rebot='docker restart astrbot && sleep 3 && docker restart cloudflared tunnel-watchdog'" >> ~/.bashrc
```

> 想彻底摆脱这一整套，具名隧道更省心（见 `NAMED_TUNNEL_SETUP.md`）：
> 域名固定，`image_host_base_url` 填死，本文所有机制都用不上。
