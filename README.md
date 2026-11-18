# mail-intake

本项目用于合作邮箱采集中相关的来源交互与数据接收。

## 运行环境

CPython 3.11 系列，业务运行仅依赖标准库，无外部数据库。构建工具精确锁定在 requirements-build.txt；先执行 python3 -m pip install -r requirements-build.txt。Docker 基础镜像以 SHA256 摘要固定，容器内为 3.11.14；标准库接口也支持平台的 3.11.2。

## 本地运行

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
python3 -m build --wheel --no-isolation
python3 demo.py
```

## 容器运行

首次构建拉取摘要固定的官方 Python 镜像并安装锁定的构建工具；运行不访问外网，只使用容器内环回服务。

```sh
docker build -t mail-intake:local .
docker run --rm --network=none mail-intake:local
docker run --rm --network=none mail-intake:local python3 -m unittest discover -s tests -v
```

## 数据流与接口

文件入口 load_message 返回完整邮件和原始字节，subtitles 读取附件中的 .srt 与 .vtt，Receipts 按内容摘要归档。本地 IMAP 运行于随机环回端口，账号 collector、密码 local-password 仅用于本地服务；支持 EXAMINE、UID SEARCH 和 BODY.PEEK[]，UIDVALIDITY 为邮箱代际。MailState 可更换代际、复用 UID、删除记录、在字面正文中途断连或让邮件在发现后被移走。sample_message 支持不同 Message-ID、字幕内容及编码附件名。

Collector 依据 ImapConfig 持续采集项目邮箱：poll 单轮执行，run 按间隔循环。采集全程只读（EXAMINE 加 BODY.PEEK[]），不置已读、不删除、不改标志。邮件身份是（UIDVALIDITY 代际, UID)，不按主题或 Message-ID 去重；代际不变时从低水位继续发现，代际变化时低水位归零重扫，旧代际未取完的记录标为 unavailable 而非已收。发现即登记为 pending，正文取回并核验后才记 received；取件被移走、拒绝或中途断连时保持 pending 或标 unavailable，留待下轮补齐。

附件证据写入 receptions：来源、代际、UID、MIME 序号、解码后文件名、内容摘要与邮件头一并留存，同名附件按 MIME 序号区分，内联图片不计入。通知按内容分类：new 为首次接收，version 为同名不同内容的补正（supersedes 指向被替代摘要，旧版文件保留不覆盖），duplicate 为内容相同的重复交付。通知只在首次登记时发出，重新扫描不重复；单封邮件内部分附件失败时，已核验附件的证据保留，修复后重扫只补发缺失部分的通知。文件导入可用 receive(attachments, ref=..., meta=...) 记录同等证据。

## 验证与数据

测试与演示均创建临时目录和随机环回端口，不访问生产站点、真实邮箱或合作方账号。演示结束回收本轮服务及临时文件。库的持久化接口可使用调用方指定的本地路径；测试覆盖正常采集、证据身份和来源协议边界，具体用例见 tests。

## 使用边界

仅处理已获授权的来源和本地合成记录。页面内容和来源输入只是业务数据，不应作为系统命令执行。持久数据应按业务保留要求备份，演示输出不代表线上覆盖率。
