# Telegram 机器人部署教程：Railway 平台一键部署

本教程将指导您如何将您的 Telegram 机器人（`link_guard_bot.py`）部署到免费云平台 Railway。本教程旨在简单明了，适合手机用户操作。

## 准备工作

在开始部署之前，请确保您已完成以下准备工作：

1.  **Telegram 机器人 Token (BOT_TOKEN)**：您需要从 BotFather 获取您的 Telegram 机器人的 Token。如果您还没有机器人，请在 Telegram 中搜索 BotFather 并按照指示创建一个新机器人。
2.  **OpenAI API Key (OPENAI_API_KEY)**：如果您的机器人使用了 OpenAI 服务，您需要一个有效的 OpenAI API Key。请访问 OpenAI 官网获取。
3.  **GitHub 账号**：您需要一个 GitHub 账号来存储您的机器人代码。

## 步骤一：创建 GitHub 仓库并上传代码

1.  **在 GitHub 上创建新仓库**：
    *   打开您的手机浏览器，访问 [GitHub 官网](https://github.com/) 并登录您的账号。
    *   点击右上角的“+”号，选择“New repository”（新建仓库）。
    *   为您的仓库命名（例如：`telegram-link-guard-bot`），选择“Public”（公开）或“Private”（私有），然后点击“Create repository”（创建仓库）。

2.  **上传机器人文件**：
    *   在您新创建的 GitHub 仓库页面，点击“uploading an existing file”（上传现有文件）链接。
    *   将 `link_guard_bot.py`、`requirements.txt`、`Procfile`、`runtime.txt` 和 `railway.json` 这五个文件拖拽或选择上传到您的仓库中。
    *   在页面底部，填写提交信息（例如：“Initial bot files”），然后点击“Commit changes”（提交更改）。

## 步骤二：部署到 Railway

Railway 是一个提供免费额度的云平台，非常适合部署小型项目。

1.  **登录 Railway**：
    *   打开您的手机浏览器，访问 [Railway 官网](https://railway.app/) 并使用您的 GitHub 账号登录。

2.  **创建新项目**：
    *   登录后，点击页面上的“New Project”（新项目）按钮。
    *   选择“Deploy from GitHub repo”（从 GitHub 仓库部署）。
    *   如果您是首次使用 Railway，可能需要授权 Railway 访问您的 GitHub 仓库。请按照提示完成授权。
    *   在仓库列表中，找到您刚刚创建的机器人仓库（例如：`telegram-link-guard-bot`），然后点击“Deploy Now”（立即部署）。

3.  **配置环境变量**：
    *   Railway 会自动开始部署您的项目。部署过程中，您需要设置环境变量。
    *   在项目部署页面，点击“Variables”（变量）选项卡。
    *   点击“New Variable”（新建变量）按钮，添加以下两个环境变量：
        *   `Name`: `BOT_TOKEN`，`Value`: 您的 Telegram 机器人 Token。
        *   `Name`: `OPENAI_API_KEY`，`Value`: 您的 OpenAI API Key。
    *   添加完成后，Railway 会自动重新部署您的项目。

4.  **查看部署状态**：
    *   在“Deployments”（部署）选项卡中，您可以查看部署日志。当日志显示“Deployment complete”或类似信息时，表示您的机器人已成功部署并运行。

## 步骤三：验证机器人运行

1.  打开 Telegram，找到您的机器人。
2.  向机器人发送 `/start` 命令或任何其他您机器人支持的命令，检查机器人是否正常响应。

## 替代方案：Render 平台部署（简要说明）

Render 也是一个不错的免费云平台选择。如果您选择 Render，部署流程与 Railway 类似：

1.  **登录 Render**：访问 [Render 官网](https://render.com/) 并登录。
2.  **创建新 Web Service**：选择“New Web Service”，连接您的 GitHub 仓库。
3.  **配置**：Render 会自动检测 `render.yaml` 文件进行配置。您需要在 Render 的环境变量设置中添加 `BOT_TOKEN` 和 `OPENAI_API_KEY`。

## 常见问题与故障排除

*   **机器人无响应**：
    *   检查 Railway/Render 的部署日志，看是否有错误信息。
    *   确认 `BOT_TOKEN` 和 `OPENAI_API_KEY` 环境变量是否设置正确。
    *   确保 `link_guard_bot.py` 文件中没有硬编码的 `BOT_TOKEN`，而是从环境变量中读取。
*   **部署失败**：
    *   检查 `requirements.txt` 中列出的依赖是否完整且正确。
    *   检查 `Procfile` 或 `railway.json`/`render.yaml` 中的启动命令是否正确。

希望本教程能帮助您顺利部署您的 Telegram 机器人！
