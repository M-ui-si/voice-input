# Qingsheng (轻声)

**Speak it. Keep it in your text field.**

**English** | [简体中文](README.zh-CN.md)

Qingsheng is a Chinese voice input tool for Windows, designed for dictating requests, coding ideas, and technical questions to browser-based agents. Start recording with the floating button or a keyboard shortcut. When you finish, the transcript is inserted at the cursor in the original text field for you to review and send.

It supports Mandarin mixed with English technical terms, with cloud transcription and a local fallback. Cloud mode transcribes audio in segments by default, and local mode also offers a cumulative preview. The transcript is inserted when recording is complete. Both modes support pause and resume. This is currently a personal trial version; the repository retains the name `voice-input`.

[Changelog (Chinese)](CHANGELOG.md) · [Local verification (Chinese)](docs/verification.md) · [Sources and licenses (Chinese)](docs/provenance.md)

## Everyday use

1. Launch Qingsheng and click the target text field on the web page so that the text cursor appears.
2. Click the button on the left of the floating bar, or press `Ctrl+Alt+Space`, to start recording.
3. Click or press the shortcut again to stop. The local preview also has a Stop recording (停止录音) button.
4. Wait for the transcript to appear in the original text field, review it, and send it yourself.

Drag the center of the floating bar to move it. Right-click or use the menu on the right to open Settings (设置), Recent text (最近文字), Cancel (取消), or Exit (退出). The shortcut is configurable, and launch at startup is disabled by default. The app's interface is currently in Chinese; key labels are included below for reference.

### Pause and resume

The preview window and right-click menu provide Pause / Resume (暂停／继续). Pausing stops microphone capture while transcription of the current trailing segment continues. Paused time is excluded from the recording and timer. Resuming keeps the same task and original text field. Clicking Stop or pressing the start/stop shortcut while paused finishes the recording and processes the remaining segments. A `Ⅱ` on the edge arrow indicates that recording is paused.

### Remember corrections from your edits

Right-click → Recent text (最近文字), select a record, and edit it. Save edits (保存修改) saves only that record's edited version; View original (查看原文) shows the original transcript. Choose Remember corrections (记住纠错), select and adjust each “incorrect term → correct term” pair, and confirm to save global rules. Candidates are unchecked by default. Single-character differences require a complete word or phrase. If a different rule already exists for the same incorrect term, the app asks whether to replace it.

Rules apply only to new recordings and can be edited or deleted under Settings → Terms and corrections (设置 → 术语与纠错). Clearing history does not remove rules, and editing history does not change text already inserted into a web page. When switching records or closing the window, you can save, discard, or return to editing.

### Collapse to the screen edge

Click Collapse (收起) on the right of the floating bar to dock it to the nearer left or right edge of the current monitor, leaving a **22 × 56 pixel arrow**. The right-click menu lets you choose either edge. Drag the arrow to change its height or move it to the other side. Clicking the arrow expands the bar without starting recording. Its position and collapsed state are remembered after restart.

Collapsing also hides the cumulative preview, and new preview results will not reopen it. Recording continues while collapsed: a red dot on the arrow indicates active recording, and `Ctrl+Alt+Space` still starts or stops it. You can also expand the bar and click Stop. Expanding restores the latest preview. Recognition failures while collapsed update the status only; expand the bar to view the error through the button or menu.

If you switch away during recognition, the result waits for up to 30 seconds. Return to the original text field to allow insertion. After the timeout, copy the text from Recent text (最近文字) or View current result (查看本次结果). Successful recognition does not automatically open a text-transfer window.

You can move the cursor, select text, or edit within the original text field. The transcript is inserted at the cursor or selection present when processing completes. Switching to another text field does not redirect the result there.

## Choose a recognition mode

| | Cloud mode | Local mode |
| --- | --- | --- |
| Engine | SiliconFlow `FunAudioLLM/SenseVoiceSmall` | Qwen3-ASR-1.7B, ONNX + GGUF q5_k |
| Initial setup | Save your own SiliconFlow API key | Download and verify the model and runtime |
| Transcript display | Cumulative segment previews by default, with insertion at completion; whole-recording mode is available in settings | Segment previews during recording, followed by confirmation and insertion after stopping |
| Network | Requires internet access and uploads recordings | Works offline once the model is ready |
| Local resources | Does not load a local inference model | Loads the model when a task starts, using RAM and possibly GPU memory |

Local mode creates a segment for roughly every 8 seconds of new audio, retaining 2 seconds of overlap for merging. This is a segmentation policy, not a guarantee of new text every 8 seconds; actual latency depends on model loading and inference speed. The model is released after 5 minutes of local inactivity, or immediately when switching to cloud mode. If GPU initialization fails, the app tries the CPU.

During local loading and recognition, the elapsed wait time updates roughly every 5 seconds. You can cancel through the right-click menu. Initial model or GPU setup may be slow. Model cleanup runs in the background on idle or exit, and driver cleanup may take a few seconds. Fixes on 2026-10-31 addressed UI freezes caused by idle checks waiting for the inference lock and by shutdown exceptions; see the [incident report (Chinese)](docs/local-hang-2026-10-31.md).

Cloud segmentation likewise submits roughly every 8 seconds of new audio, with up to 2 seconds of overlap on subsequent segments. Requests run sequentially, and stopping or pausing submits the trailing segment. The complete recording remains on the computer for recovery. When a boundary cannot be reliably deduplicated, both sides are retained, with a review prompt in the preview, completion status, and history. Slow cloud processing can create a backlog; 8 seconds is not a response-time guarantee. Uncheck Transcribe while recording in the cloud (云端边录边识别) in settings to upload the whole recording after stopping instead.

### Set up cloud transcription

Register with SiliconFlow, complete any identity verification required by the provider, and save your own API key under Settings → Input and cloud (设置 → 输入与云端).

A green Saved and encrypted (已保存并加密) status confirms success. The field is intentionally blank when you reopen settings: saved keys are never displayed. Saving a blank field keeps the existing key; only Delete key (删除密钥) removes it.

The app enables only the allowlisted SenseVoiceSmall model and checks the official free pricing at startup and on the first use each day. If it cannot confirm that calls are free, it pauses requests rather than switching to a paid model. Cloud Qwen is not enabled. The app does not promise that the provider will remain free; account access and availability are controlled by SiliconFlow.

### Prepare the local fallback

Open Settings → Local fallback (设置 → 本地备用) and choose Prepare / Resume local model download (准备 / 继续下载本地模型), or import an existing model directory. Downloads support pause and resume and are verified against pinned SHA-256 hashes. The model archive is about 1.95 GB; extracted files and the runtime require additional disk space. File size does not indicate runtime memory usage.

Once the model is ready, select Local accuracy mode (本地准确模式) and save. This option's name does not mean that quantitative accuracy acceptance testing has passed; test it with your own dictation.

## Recent updates

- **Direct browser insertion:** supports rich-text editors and returning to the original field after temporary focus loss. Editing within that field does not invalidate the task.
- **Cloud recovery:** retries HTTP 500 / 502 / 503 / 504 up to twice, re-uploading the complete recording each time. You can cancel or choose local recognition after continued failure.
- **Failed recording retention:** recordings survive app exit after a failure. Manual retries still validate the original editor's identity.
- **Accessible preview controls:** the preview adjusts near screen edges and follows dragging. A dedicated Stop button is available, and repeated clicks do not restart recording.
- **Saved corrections and pause/resume:** edited history retains the original transcript, correction rules require individual confirmation, cloud mode provides segment previews, and neither mode captures audio while paused.

See the [changelog (Chinese)](CHANGELOG.md) for details.

## Frequently asked questions

**Why wasn't the transcript inserted directly?**

Make sure you clicked into a text field before recording. Return to that same field after processing and wait for insertion. After 30 seconds, copy from history instead. Manual pasting may be necessary when the app cannot confirm an editable control, when the clipboard contains special formats, or when the target window runs as administrator. The app does not force focus changes or send messages automatically.

**What if the cloud returns HTTP 500 or the network disconnects?**

Temporary server errors receive a limited number of retries. Network errors, account permissions, and rate limits produce a message. Choose Retry cloud (重试云端) or Local recognition (本地识别) without recording again. Failure does not automatically load the local model. Invalid keys, quota errors, and permission errors are not repeatedly retried.

If cloud segments keep failing during recording, further cloud requests pause and the app shows a warning while the microphone continues recording. You can still pause or stop. After stopping, choose cloud retry, local recognition, or discard; incomplete segment results are not automatically inserted. Retrying processes the complete recording from scratch without mixing in text from the previous attempt. A real cloud test on 2026-10-27 returned HTTP 402, so real-service acceptance of segmentation and its speed benefits remains incomplete.

**How do I recover a failed recording after exiting?**

Right-click the floating bar → Recent text → Recover temporary recording (最近文字 → 恢复暂存录音), choose a WAV file, and continue with the current recognition mode. Temporary recordings are retained for up to 24 hours, so recover them promptly. Explicit cancellation or Discard and record again (丢弃并重录) deletes the current recording.

**Why do rapid clicks produce “No valid speech recorded” (未录到有效语音)?**

Very short recordings may end before any audio arrives. The app closes the old preview and returns to a state where you can start again. Stop requests after recording begins are not ignored by rapid-click protection. Clicks immediately after expanding or collapsing are briefly guarded to avoid accidentally activating recording or menu controls in the new layout.

**Why aren't my custom terms enforced?**

Default terms include Codex, Claude, GitHub, API, MCP, Python, IDA, Ghidra, GDB, ELF, PE, ROP, ASLR, DEP, and UAF. The cloud integration does not pass hotword parameters. Local mode also avoids passing the entire vocabulary to the decoder, which could cause the vocabulary itself to appear in the transcript.

Add confirmed `incorrect term=correct term` rules under Terms and corrections (术语与纠错). Incorrect terms must contain at least two characters. English replacements respect word boundaries. The app does not use fuzzy replacement or LLM rewriting.

## Data and privacy

- Cloud mode uploads recordings to SiliconFlow. Local mode recognizes speech on the computer through private process pipes without opening a network service port.
- API keys are encrypted with Windows DPAPI and bound to the current Windows user. Enter your key again when moving to another computer.
- Recent text is retained for 7 days. Recordings are deleted after successful recognition and result storage; failed recordings are retained for up to 24 hours. Settings let you clear text and recordings.
- Pasting attempts to preserve and restore the clipboard, giving priority to anything you copy during the operation. If a safe backup is not possible, automatic pasting is skipped.
- Deleting local records does not delete data held by the provider. Cloud data handling is subject to the provider's policies.

The repository excludes personal keys, recordings, history databases, desktop screenshots, models, and build artifacts. Do not upload your own `data/` directory.

## Run from source

This repository provides source code. This update does not include a downloadable EXE. You need Windows x64, Python 3.14, and the pinned upstream source.

```powershell
git clone --recurse-submodules https://github.com/M-ui-si/voice-input.git
cd voice-input
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe main.py --settings
```

If you cloned without the upstream source, run `git submodule update --init --recursive`. Keep the pinned commit rather than updating directly to the latest upstream branch.

### Build and check

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
.\.venv\Scripts\python.exe -X utf8 scripts\package_probe.py
```

Build output goes to `dist-review\Speak\`. After building, double-click `启动轻声.vbs` in the repository root or run `dist-review\Speak\Speak.exe`. Preserve the project layout and `_internal` dependencies: `portable-root.json` points data and model locations to the project root, so copying the EXE alone is insufficient.

The `scripts/` directory includes probes for native input, Edge / Grok draft insertion, preview buttons, and packaged startup. `cloud_recovery_probe.py --mode cloud` generates fixed test speech and calls the cloud service; `evaluate.py` evaluates real recordings against human reference transcripts. Avoid other desktop interactions while running interactive probes.

## Verification status

Local records as of 2026-10-31, with additional local recognition and exit checks in the [incident report (Chinese)](docs/local-hang-2026-10-31.md):

| Area | Results and limits |
| --- | --- |
| Automated regression | 144 tests passed, covering history corrections, segmented pause/resume, failed recording retention, stale-task isolation, local model shutdown, UI refresh protection, and more |
| Browser insertion | Passed native EDIT, an isolated Edge rich-text page, and Grok web draft checks; used simulated recognition text and sent no messages |
| Real cloud API | On October 22, successfully transcribed about 5 seconds of synthetic Chinese speech in about 2 seconds; this does not establish accuracy for human speech or performance on long recordings |
| Preview interaction | Start/stop and preview Stop button clicks passed at 4 physical screen positions; used simulated tasks |
| Edge docking | Left/right arrow clicks, dragging, restart persistence, physical shortcuts, and focus retention passed; used simulated tasks |
| Rapid clicks | 120 desktop clicks, empty-input recovery, and result-window deduplication passed; used a simulated recorder |
| Recording button | 18 rounds of rapid start/stop with a real microphone and 10 repeated button clicks passed; no recognition or upload, and test audio was deleted |
| Added desktop flows | Actual pause/resume clicks, single insertion, history editing, and rule confirmation passed with synthetic PCM and simulated ASR; no callback errors |
| Paused capture | Real microphone frame counts stayed unchanged while paused and continued normally after resume; no recognition or upload, and test audio was deleted |
| Cloud acceptance limit | The first 60-second synthetic audio request returned HTTP 402, and no further requests were made; 1 / 3 / 5 minute measurements and speed conclusions remain pending |
| Fix build | Local EXE startup, exit, and UI callback checks passed; the idle test did not load a local model |
| Still to verify | Human Mandarin and technical-term accuracy, 1 / 3 / 5 minute latency, peak GPU memory usage, and more real websites |

Numbers, negations, and security terminology still need human review. See the [verification records (Chinese)](docs/verification.md) for evidence, history, and limitations.

## Foundation and licenses

Based on [CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline), pinned to commit `84912d5218ee5e51e216c54dc15a1cb0f466eb76`, with inference runtimes from upstream v2.6. This project adds desktop interactions, cloud integration, recording queues, target validation, and task management.

The upstream source uses MIT. The original Qwen3-ASR model is documented under Apache-2.0 in its model card. Runtimes, converted models, and Python dependencies retain their respective licenses. See the [provenance records (Chinese)](docs/provenance.md) for versions, sources, checksums, and redistribution limits.

References: [Transcription API](https://docs.siliconflow.cn/docs/api/audio-transcriptions-post) · [Official pricing](https://siliconflow.cn/pricing) · [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)
