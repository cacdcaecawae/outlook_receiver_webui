# Outlook Receiver Web UI

独立于 `openai_reg-main` 的本地 Web UI 接码器，使用 `uv` 管理。

它会读取 Outlook 账号列表，在浏览器里显示成控制台界面，选择一个账号后点击“开始监听”即可自动等待最新验证码。

## 启动

```bash
cd E:\注册机codex\outlook_receiver_webui
uv run python app.py
```

默认地址：

```text
http://127.0.0.1:8765
```

## 指定账号文件

默认只读取当前目录下的：

```text
E:\注册机codex\outlook_receiver_webui\outlook_accounts.txt
```

如果你想手动指定别的文件，也可以：

```bash
uv run python app.py --accounts-file "E:\注册机codex\openai_reg-main\outlook_accounts.txt"
```

## 账号文件格式

推荐格式：

```text
email----password----client_id----refresh_token
```

也支持：

```text
email:password:client_id:refresh_token
```

## 功能

- 左侧账号列表
- 中间大号验证码卡片
- 顶部账号统计和状态徽标
- 显示当前账号、发件人、邮件主题、收到时间
- 支持单账号开始监听和停止监听

## 验证

```bash
uv run python -m unittest
uv run python -m py_compile app.py receiver_core.py
```
