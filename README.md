# mail-intake

本项目用于合作邮箱采集中相关的来源交互与数据接收。

## 运行环境

CPython 3.11 系列，仅依赖标准库；没有需要安装的 Python 包或外部数据库。Docker 基础镜像以 SHA256 摘要固定，容器内为 3.11.14；标准库接口也支持平台的 3.11.2。

## 本地运行

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
python3 demo.py
```

## 容器运行

本地需已存在 `python:3.11.14-slim-bookworm` 镜像。构建不下载依赖，运行只访问容器内环回服务。

```sh
docker build --network=none -t mail-intake:local .
docker run --rm --network=none mail-intake:local
docker run --rm --network=none mail-intake:local python3 -m unittest discover -s tests -v
```

## 数据流与接口

文件入口 load_message 返回完整邮件和原始字节，subtitles 读取附件中的 .srt 与 .vtt，Receipts 按内容摘要归档。本地 IMAP 运行于随机环回端口，账号 collector、密码 local-password 仅用于本地服务；支持 EXAMINE、UID SEARCH 和 BODY.PEEK[]，UIDVALIDITY 为邮箱代际。MailState 可更换代际、复用 UID、删除记录或在字面正文中途断连。sample_message 支持不同 Message-ID、字幕内容及编码附件名。

## 验证与数据

测试与演示均创建临时目录和随机环回端口，不访问生产站点、真实邮箱或合作方账号。演示结束回收本轮服务及临时文件。库的持久化接口可使用调用方指定的本地路径；测试覆盖正常采集、证据身份和来源协议边界，具体用例见 tests。

## 使用边界

仅处理已获授权的来源和本地合成记录。页面内容和来源输入只是业务数据，不应作为系统命令执行。持久数据应按业务保留要求备份，演示输出不代表线上覆盖率。
