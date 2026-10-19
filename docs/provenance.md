# 来源与版本记录

本项目轻声 Speak 0.1.0，为个人 Windows 桌面使用版本；改动包括云端适配、桌面界面、录音队列、目标位置检查、剪贴板保护和本地进程管理。

| 组成 | 固定来源 / 版本 | 许可证 |
| --- | --- | --- |
| CapsWriter-Offline 源码 | [官方仓库](https://github.com/HaujetZhao/CapsWriter-Offline)，提交 `84912d5218ee5e51e216c54dc15a1cb0f466eb76` | MIT，全文在 `upstream/LICENSE`，打包收录到 `_internal/licenses` |
| Windows 推理运行库 | [v2.6 发布](https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/v2.6)，`CapsWriter-Offline-20260914.zip` | 上游发布包含多项第三方组件，原始发布和许可保留在 `runtime`；个人使用，不宣称已完成公共再发行许可审核 |
| Qwen3-ASR-1.7B 原始模型 | [Qwen 官方模型卡](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) | Apache-2.0，按官方模型卡，2026-10-18 查验 |
| ONNX / GGUF q5_k 转换模型 | [CapsWriter 官方 models 发布](https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models)，`Qwen3-ASR-1.7B-q5_k.zip` | 基于上述模型；保留压缩包内随附文件 |
| Python 依赖 | 精确安装版本在 `requirements.lock.txt` | 各依赖原有许可证；构建时收集已安装包的许可证到 `dist-review/Speak/licenses/python` |

固定下载 SHA-256：

```text
CapsWriter-Offline-20260914.zip
40a46100e2b221ed487d85aa923a38f8f722090bf1fa86975b701baceafbb6f4

Qwen3-ASR-1.7B-q5_k.zip
f40040fe62a5ef0c09f8699fdbcb30f18bb8ae2bcd515ed4954e1f62b8b0e88f
```

来源哈希固定在 `speak/prepare.py`，只从上游官方发布下载；分段合并后校验完整压缩包，校验不通过不解压。手动导入模型会明确记录为用户导入、未经发布者哈希校验。

云端使用[硅基流动转写接口](https://docs.siliconflow.cn/docs/api/audio-transcriptions-post)。2026-10-18 文档明确列出 SenseVoiceSmall，未列出 Qwen3-ASR；代码暂不开放云端 Qwen。免费检查使用[官方价格页](https://siliconflow.cn/pricing)，启动及每日首次使用复核，检查失败停止云端请求。价格检查不等于账号权限、额度、准确率或长期免费保证。
