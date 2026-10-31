import copy
import queue
import threading
import time
import wave
from types import SimpleNamespace

import pytest

from speak.controller import Controller, Task
from speak.merging import merge_cloud_text
from speak.providers import RecognitionError, Result
from speak.recording import RATE, Recorder, SegmentWriter, split_live_wav
from speak.storage import History, Settings


def pcm(path):
    with wave.open(str(path), "rb") as wav:
        return wav.readframes(wav.getnframes())


@pytest.mark.parametrize("seconds,lengths", [(0, []), (3, [3]), (8, [8]), (16, [8, 10]), (17, [8, 10, 3])])
def test_live_and_recovery_windows_are_sample_accurate(tmp_path, seconds, lengths):
    segments = []
    writer = SegmentWriter(tmp_path / "all.wav", segments.append)
    data = b"".join((i+1).to_bytes(2, "little") * RATE for i in range(seconds))
    writer.feed(data); writer.flush()
    assert [(s.end_frame-s.start_frame)/RATE for s in segments] == lengths
    assert b"".join(pcm(s.path)[s.overlap_frames*2:] for s in segments) == data
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as out:
        out.setparams((1, 2, RATE, 0, "NONE", "not compressed")); out.writeframes(data)
    restored = list(split_live_wav(source, tmp_path))
    assert [pcm(s.path) for s in restored] == [pcm(s.path) for s in segments]


def test_pause_drains_tail_and_does_not_record_during_pause(tmp_path, monkeypatch):
    import sounddevice
    driver = {}
    class Stream:
        def __init__(self, **kw):
            driver["callback"] = kw["callback"]; self.active = False
        def start(self): self.active = True
        def stop(self): self.active = False
        def close(self): self.active = False
    monkeypatch.setattr(sounddevice, "RawInputStream", Stream)
    segments = []
    recorder = Recorder(tmp_path / "all.wav", on_segment=segments.append)
    recorder.start()
    feed = lambda value, seconds: driver["callback"](value.to_bytes(2, "little") * RATE * seconds, 0, None, None)
    feed(1, 9); recorder.pause()
    assert recorder.paused and not recorder.stream.active and recorder.frames == 9*RATE
    feed(99, 3)
    assert recorder.duration == 9
    recorder.pause()  # Idempotent; do not flush an overlap-only fragment.
    recorder.continue_recording(); feed(2, 3); recorder.pause(); recorder.stop(); recorder.stop()
    assert pcm(recorder.path) == b"\x01\x00" * RATE*9 + b"\x02\x00" * RATE*3
    assert [len(pcm(s.path))//(RATE*2) for s in segments] == [8, 3, 3]
    assert [s.overlap_frames for s in segments] == [0, 2*RATE, 0]
    assert segments[-1].break_before and recorder.duration == 12


@pytest.mark.parametrize("previous,incoming,overlap,expected,review", [
    ("请不要发送 API。", "不要发送 API，然后检查。", 32000, "请不要发送 API，然后检查。", False),
    ("不要发送", "不要发送消息", 0, "不要发送不要发送消息", False),
    ("请检查 MCP", "MCP 服务", 32000, "请检查 MCP MCP 服务", True),
    ("值为 -10.5", "10.5，不要发送", 32000, "值为 -10.5 10.5，不要发送", True),
    ("不要发送", "不要发送不要发送消息", 32000, "不要发送不要发送不要发送消息", True),
    ("保留 API2", "API20 是另一个", 32000, "保留 API2 API20 是另一个", True),
    ("请不要删除", "不要删除", 32000, "请不要删除不要删除", True),
])
def test_merge_never_fuzzily_drops_words(previous, incoming, overlap, expected, review):
    assert merge_cloud_text(previous, incoming, overlap) == (expected, review)


def controller(tmp_path, mode="cloud"):
    c = object.__new__(Controller)
    c.settings = Settings(mode=mode)
    c.state = "recording"
    c.events = queue.Queue()
    c.history = History(tmp_path)
    c.local = SimpleNamespace(stop=lambda: None)
    c.cloud = SimpleNamespace()
    c.task = Task("task", c.history.audio_dir / "all.wav", copy.deepcopy(c.settings), target="editor")
    with wave.open(str(c.task.path), "wb") as out:
        out.setparams((1, 2, RATE, 0, "NONE", "not compressed")); out.writeframes(b"\x01\x00" * RATE*17)
    c.task.target_window = (1, 2)
    c.task.files = []
    return c


def wait_until(predicate, timeout=3):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError("worker did not reach expected state")
        time.sleep(.005)


def test_cloud_preview_while_recording_and_single_final_result(tmp_path):
    c = controller(tmp_path)
    segments = list(split_live_wav(c.task.path, c.history.audio_dir))
    outputs = iter(["第一段不要发送消息", "不要发送消息，然后检查 API", "检查 API 的数值"])
    c.cloud.transcribe = lambda *a, **kw: Result(next(outputs), "cloud", "test", .1)
    worker = threading.Thread(target=c._run, args=(c.task,)); worker.start()
    c.task.segments.put(segments[0])
    wait_until(lambda: c.task.processed_segments == 1)
    assert worker.is_alive() and not c.history.list() and c.task.path.exists()
    c.task.segments.put(segments[1]); c.task.segments.put(segments[2]); c.task.stopped.set(); c.task.segments.put(None)
    worker.join(3)
    events = list(c.events.queue)
    assert len([e for e in events if e[1] == "preview"]) == 3
    results = [e[2] for e in events if e[1] == "result"]
    assert len(results) == 1 and len(c.history.list()) == 1
    assert results[0].text == "第一段不要发送消息，然后检查 API 的数值"
    assert not c.task.path.exists() and not any(s.path.exists() for s in segments)


def test_cloud_failure_waits_for_user_stop_without_uploading_later_segments(tmp_path):
    c = controller(tmp_path)
    segments = list(split_live_wav(c.task.path, c.history.audio_dir))
    calls = []
    def fail(*a, **kw):
        calls.append(1); raise RecognitionError("network", "网络失败")
    c.cloud.transcribe = fail
    worker = threading.Thread(target=c._run, args=(c.task,)); worker.start()
    c.task.segments.put(segments[0]); c.task.segments.put(segments[1])
    wait_until(lambda: c.task.cloud_error is not None)
    assert c.state == "recording" and worker.is_alive() and calls == [1]
    assert not any(e[1] in ("error", "result") for e in c.events.queue)
    c.task.stopped.set(); c.task.segments.put(None); worker.join(3)
    assert c.task.path.exists() and not c.history.list()
    assert [e[1] for e in c.events.queue].count("error") == 1
    assert not any(e[1] == "result" for e in c.events.queue)


def test_cancelled_inflight_segment_cannot_add_history_or_result(tmp_path):
    c = controller(tmp_path)
    segment = next(split_live_wav(c.task.path, c.history.audio_dir))
    gate = threading.Event(); entered = threading.Event()
    def delayed(*a, **kw):
        entered.set(); gate.wait(3); return Result("迟到结果", "cloud", "test", .1)
    c.cloud.transcribe = delayed
    c.task.segments.put(segment)
    worker = threading.Thread(target=c._run, args=(c.task,)); worker.start()
    assert entered.wait(3)
    c.cancel(); gate.set(); worker.join(3)
    assert not c.history.list() and not c.task.path.exists()
    assert not any(e[1] == "result" for e in c.events.queue)


def test_local_pause_boundary_does_not_merge_separate_utterances(tmp_path):
    c = controller(tmp_path, "local")
    writer = SegmentWriter(c.task.path.with_name("test.wav"), c.task.segments.put)
    writer.feed(b"\x01\x00"*RATE); writer.flush(boundary=True)
    writer.feed(b"\x02\x00"*RATE); writer.flush()
    previous = []
    def recognize(path, terms, text, *a):
        previous.append(text); return Result("不要发送", "local", "test", .1)
    c.local.segment = recognize
    c.task.stopped.set(); c.task.segments.put(None); c._run(c.task)
    assert previous == ["", ""]
    assert c.history.list()[0][2] == "不要发送不要发送"


def test_retry_restarts_cloud_sequence_and_preserves_editor(tmp_path):
    c = controller(tmp_path)
    c.state = "error"
    c.cloud.transcribe = lambda *a, **kw: Result("识别文本", "cloud", "test", .1)
    c.retry("cloud")
    wait_until(lambda: any(e[1] == "result" for e in c.events.queue))
    assert c.task.target == "editor" and c.task.target_window == (1, 2)
    assert c.task.processed_segments == 3 and len(c.history.list()) == 1


def test_resume_failure_preserves_captured_audio(tmp_path):
    c = controller(tmp_path)
    c.state = "paused"
    c.task.recorder = SimpleNamespace(continue_recording=lambda: (_ for _ in ()).throw(OSError()),
                                     error=None, stop=lambda: None, segments=[])
    c.continue_recording()
    assert c.state == "processing" and c.task.stopped.is_set()
    c.task.settings.cloud_segmented = False
    c._run(c.task)
    assert c.task.error_code == "recording" and c.task.path.exists()


def test_paused_task_blocks_start_and_file_recovery(tmp_path):
    c = controller(tmp_path)
    c.state = "paused"
    original = c.task
    c.start("other-editor")
    c.resume(tmp_path / "does-not-exist.wav", "cloud")
    assert c.task is original and c.state == "paused"


def test_old_preview_actions_cannot_pause_or_stop_a_new_task():
    from speak.ui import App
    app = SimpleNamespace(controller=SimpleNamespace(state="recording", task=SimpleNamespace(id="new")))
    App.toggle_pause(app, "old", "recording")
    App.stop_preview_recording(app, "old")
    App.toggle_pause(app, "new", "paused")
    assert app.controller.state == "recording"


def test_rules_are_snapshotted_and_preview_does_not_chain_replacements(tmp_path):
    c = controller(tmp_path)
    c.task.settings.replacements = {"AA": "BB", "BB": "CC"}
    c.settings.replacements = {"AA": "later-user-choice"}
    segment = next(split_live_wav(c.task.path, c.history.audio_dir))
    c.cloud.transcribe = lambda *a, **kw: Result("AA BB", "cloud", "test", .1)
    c.task.segments.put(segment); c.task.stopped.set(); c.task.segments.put(None)
    c._run(c.task)
    preview = [e[2] for e in c.events.queue if e[1] == "preview"]
    assert preview == ["BB CC"] and c.history.list()[0][2] == "BB CC"


def test_cloud_whole_fallback_still_waits_until_stop(tmp_path):
    c = controller(tmp_path)
    c.task.settings.cloud_segmented = False
    called = threading.Event()
    c.cloud.transcribe = lambda *a, **kw: called.set() or Result("整段文本", "cloud", "test", .1)
    worker = threading.Thread(target=c._run, args=(c.task,)); worker.start()
    assert not called.wait(.15)
    c.task.stopped.set(); worker.join(3)
    assert called.is_set() and c.history.list()[0][2] == "整段文本"


def test_cloud_failure_on_shutdown_preserves_full_recording(tmp_path):
    c = controller(tmp_path)
    c.task.cloud_error = RecognitionError("quota", "测试失败")
    c.shutdown()
    assert c.task.path.exists() and c.task.preserve_audio


def test_evaluation_records_blocking_error_without_more_uploads(tmp_path, monkeypatch):
    import json
    from scripts import evaluate
    c = controller(tmp_path)
    calls = []
    def fail(*a, **kw):
        calls.append(1); raise RecognitionError("quota", "免费服务不可用")
    monkeypatch.setattr(evaluate, "CloudProvider", lambda _: SimpleNamespace(transcribe=fail, request_count=0))
    monkeypatch.setattr(evaluate, "SecretStore", lambda: None)
    manifest, output = tmp_path / "manifest.json", tmp_path / "evaluation.json"
    manifest.write_text(json.dumps([{"audio": str(c.task.path), "reference": "测试", "split": "acceptance"}]), "utf-8")
    monkeypatch.setattr("sys.argv", ["evaluate.py", str(manifest), "--mode", "cloud", "--cloud-strategy", "compare", "--output", str(output)])
    with pytest.raises(SystemExit):
        evaluate.main()
    report = json.loads(output.read_text("utf-8"))
    assert report["status"] == "blocked" and report["failure"]["error_code"] == "quota"
    assert calls == [1] and report["samples"] == []
