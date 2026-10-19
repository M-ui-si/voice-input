# 轻声

**说话即输入 · 中文语音输入工具**

面向 Windows 的中文语音输入工具。免费云端优先，本地模型按需加载；识别结果填入当前输入框，由你检查和发送。

## 使用

1. 双击 `启动轻声.vbs`，或直接运行 `dist-review\Speak\Speak.exe`。
2. 首次打开设置，在“输入与云端”中填写自己的硅基流动 API Key，保存。账号注册及实名认证在服务商网站完成，不要把密钥发到聊天里。
   保存成功后会显示绿色“已保存并加密”。再次打开时 Key 输入框为空是正常的，留空会继续使用原密钥；只有“删除密钥”会清除它。
3. 点击 Agent 的文字输入框，再点击悬浮按钮，或按 `Ctrl+Alt+Space` 开始；再次点击／按键结束。
4. 等待文字填入，检查后自行发送。目标输入位置发生变化或无法确认时，结果会留在本次结果窗口与历史中，点击“复制文字”即可。

   云端模式在停止录音后转写整段；本地模式会每约 8 秒更新旁边的实时预览，停止后再确认完整结果并填入。

悬浮条中间区域可以拖动；右键或点击右侧菜单进入设置、最近文字、取消或退出。

## 免费云端与本地备用

- 云端仅启用 `FunAudioLLM/SenseVoiceSmall`，每次启动和每天首次使用核对硅基流动官方价格页。无法确认免费时停止调用。不启用会自动转付费的试用接口。
- 云端 Qwen 的价格页和 API 文档不一致，尚未通过账号实测与口述验收，不开放选择。
- 云端模式会将录音上传给硅基流动；本地历史删除不代表服务商侧数据删除，服务商规则见其隐私政策。
- 本地使用 CapsWriter-Offline 的 Qwen3-ASR-1.7B q5_k 引擎，模型压缩包约 1.95GB。设置中的“本地备用”可断点续传、校验或导入模型。
- 本地模型尚未下载完整时，不能断网识别。下载可暂停并保留分段进度；成功校验后才会显示文件就绪。
- 本地模型只在本地任务开始时加载，GPU 初始化失败会尝试 CPU；空闲 5 分钟后退出识别子进程释放内存与显存，切回云端立即释放。
- 云端失败不自动启动本地模型。当前录音会保留，选择“本地识别”即可重试，无需重新录音。

## 文字与隐私

- 保留原意和标点，不进行 LLM 改写，不自动按回车。
- 内置常用 Agent／二进制安全术语；云端不虚构热词参数。可在“术语与纠错”添加确认过的 `错词=正确词` 规则。
- 最近文字保存 7 天；成功后删除本地录音，失败录音最多保留 24 小时。退出时取消的当前任务会删除录音。设置可清空记录、删除密钥。
- API Key 用 Windows DPAPI 加密。软件目录的 `data` 不应公开发布。
- 自动粘贴前核对窗口、输入控件和文字内容；录音期间可以在同一个输入框移动光标或选择文本，完成后按当时的位置插入。若切换输入框或文档内容变化，则保留结果供复制。支持常见文本、HTML 和文件剪贴板恢复；无法安全保存的特殊剪贴板格式会改为手动复制。
- 本地模式不把整张个人术语表作为模型上下文，以免模型把术语表本身转写出来；云端接口也不支持热词参数。纠错规则仅接受至少两个字符的错词。
- 普通权限应用不能保证向管理员窗口输入；第一版不默认请求管理员权限。

## 开发

源码仓库不包含 EXE、模型、运行库下载缓存或个人数据。首次克隆需带上固定版本的上游子模块，再按下面步骤安装依赖和构建：

```powershell
git clone --recurse-submodules https://github.com/M-ui-si/voice-input.git
cd voice-input
```

已经普通克隆的目录执行 `git submodule update --init --recursive`。上游固定提交为 `84912d5218ee5e51e216c54dc15a1cb0f466eb76`，不要直接更新到上游最新版本。

需要 Windows x64、Python 3.14 和项目隔离环境。依赖版本见 `requirements.lock.txt`。首次创建环境：

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

已有本项目环境时执行：

```powershell
.\.venv\Scripts\python.exe main.py --settings
.\.venv\Scripts\python.exe -m pytest -q
.\build.ps1
```

保留整个 `D:\speak` 目录使用，不能只拷贝 EXE：`dist-review\Speak\_internal` 是运行依赖，`portable-root.json` 将数据和模型位置指向项目根目录。迁移电脑时不复制 `data\credential.dpapi`，请重新保存密钥。

上游固定为 CapsWriter-Offline 提交 `84912d5218ee5e51e216c54dc15a1cb0f466eb76`，运行库来自 v2.6。源码及 MIT 许可证保存在 `upstream`，引擎运行时复用上游代码；桌面控制、云端适配、录音队列、窗口校验及安全剪贴板为本项目实现。

本地识别采用父子进程私有 IPC，不开放网络端口，比只监听回环地址进一步收紧访问范围。后台每约 8 秒形成一个音频窗口、保留 2 秒重叠，逐步更新本地预览并复用上游合并逻辑；云端停止后上传整段。SiliconFlow 当前文档描述的是整段 POST 转写接口，未描述 ASR 实时流式响应。

## 验证边界

请查看 `docs/verification.md` 和 `docs/review-2026-10-19.md`。本次代码审查后 46 项自动化回归通过，受控原生文本框和 Edge 本地页面的插入检查通过。用户已试用识别成功；准确率、延迟和真实 Agent 站点兼容性仍需使用独立真人样本测量。

真人样本评估工具：`scripts/evaluate.py`，输入带人工参考原文的 WAV 清单，分别运行云端／本地并保存错误率、术语命中率及耗时。关键数字、否定词和最终语义仍需人工核对。

清单示例见 `docs/acceptance.example.json`，录音须为 16kHz、单声道、PCM16 WAV。把示例路径和参考原文换成实际录音后运行：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py docs\acceptance.example.json --mode cloud --output data\evaluation-cloud.json
.\.venv\Scripts\python.exe scripts\evaluate.py docs\acceptance.example.json --mode local --output data\evaluation-local.json
```

版本、模型来源及许可证记录见 `docs/provenance.md`。

来源：[CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline)、[硅基流动语音接口](https://docs.siliconflow.cn/docs/api/audio-transcriptions-post)、[官方价格](https://siliconflow.cn/pricing)、[Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)。
