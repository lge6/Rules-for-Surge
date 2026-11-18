# Surge-Rule

个人使用的 Direct 和 Proxy 规则，支持 Surge、Loon 和 Clash / Mihomo。

| 客户端 | Direct | Proxy |
| --- | --- | --- |
| Surge | [Direct.list](https://raw.githubusercontent.com/lge6/Rules-for-Surge/main/surge/Direct.list) | [Proxy.list](https://raw.githubusercontent.com/lge6/Rules-for-Surge/main/surge/Proxy.list) |
| Loon | [Direct.list](https://raw.githubusercontent.com/lge6/Rules-for-Surge/main/loon/Direct.list) | [Proxy.list](https://raw.githubusercontent.com/lge6/Rules-for-Surge/main/loon/Proxy.list) |
| Clash / Mihomo | [Direct.yaml](https://raw.githubusercontent.com/lge6/Rules-for-Surge/main/clash/Direct.yaml) | [Proxy.yaml](https://raw.githubusercontent.com/lge6/Rules-for-Surge/main/clash/Proxy.yaml) |

任意修改一份同名规则并提交到 `main`，GitHub Actions 会同步到其他客户端目录。Direct 和 Proxy 分别同步。新增、删除、调整顺序和清空规则都会传播，客户端在刷新订阅后取得更新。

根目录的 `Direct.list`、`Proxy.list` 也参与同步，原有订阅链接可以继续使用。文件名和目录名区分大小写。

同一批尚未完成同步的修改，如果涉及同类规则的多份文件，且最终内容不同，Actions 会显示冲突并停止写入。将涉及的同类文件调整为相同名单后重新提交，即可继续同步。Direct 与 Proxy 可以同时分别在不同客户端文件里修改。

同步记录保存在 `.rules-sync.json`，由程序随规则一起更新。Actions 在写入前读取 `main` 最新版本，遇到提交竞争会重新读取并重试。

当前支持 `DOMAIN`、`DOMAIN-SUFFIX`、`DOMAIN-KEYWORD`、`IP-CIDR`、`IP-CIDR6`、`GEOIP`、`IP-ASN`，IP 类规则支持 `no-resolve`。名单内每条规则省略策略，策略在客户端配置中指定。整行 `#` 注释和规则顺序会一同同步。域名统一为小写，国际化域名转换为 ASCII，IP 网段规范化后输出。

清空 Surge / Loon 规则时保留空文件，清空 Clash 规则时使用 `payload: []`。遇到尚未支持的规则类型、参数或无效内容，Actions 会给出位置并报错。

## 客户端引用

将下方的 `PROXY` 替换为自己已有的代理策略或策略组名称。

Surge 在 `[Rule]` 中添加以下内容。

```ini
RULE-SET,https://raw.githubusercontent.com/lge6/Rules-for-Surge/main/surge/Direct.list,DIRECT
RULE-SET,https://raw.githubusercontent.com/lge6/Rules-for-Surge/main/surge/Proxy.list,PROXY
```

Loon 在 `[Remote Rule]` 中添加以下内容。

```ini
https://raw.githubusercontent.com/lge6/Rules-for-Surge/main/loon/Direct.list,policy=DIRECT,enabled=true
https://raw.githubusercontent.com/lge6/Rules-for-Surge/main/loon/Proxy.list,policy=PROXY,enabled=true
```

Clash / Mihomo 使用 `classical` YAML 规则集合，将以下配置合入主配置的对应字段。

```yaml
rule-providers:
  custom-direct:
    type: http
    behavior: classical
    path: ./ruleset/custom-direct.yaml
    url: https://raw.githubusercontent.com/lge6/Rules-for-Surge/main/clash/Direct.yaml
    interval: 86400
  custom-proxy:
    type: http
    behavior: classical
    path: ./ruleset/custom-proxy.yaml
    url: https://raw.githubusercontent.com/lge6/Rules-for-Surge/main/clash/Proxy.yaml
    interval: 86400

rules:
  - RULE-SET,custom-direct,DIRECT
  - RULE-SET,custom-proxy,PROXY
```

把引用放在需要优先于其他规则的位置。上例中 Direct 优先于 Proxy。

## 本地维护

需要 Python 3.10 或更新版本。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r scripts/requirements.txt
.venv/bin/python scripts/sync_rules.py
.venv/bin/python scripts/sync_rules.py --check
.venv/bin/python -m unittest discover -s tests -v
```

提交修改后的规则及程序生成的同步文件。只在 GitHub 网页编辑一份规则时，可以直接提交，由 Actions 完成后续同步。

工作流使用仓库自带的 `GITHUB_TOKEN`，同步任务请求 `contents: write` 权限。使用此 Token 产生的同步提交会避免重复触发 push 工作流。Pull request 会运行校验和同步预演，合入 `main` 后发布同步结果。

语法依据可查阅 [Surge 规则集](https://manual.nssurge.com/rules/ruleset.html)、[Loon 订阅规则](https://nsloon.app/docs/Rule/sub_rule/)、[Mihomo 规则集合](https://wiki.metacubex.one/config/rule-providers/content/) 和 [GitHub Actions 触发说明](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)。
