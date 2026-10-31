import sqlite3
import time

import pytest

from speak.corrections import correction_candidates, merge_rule_edits, validate_rule
from speak.providers import Result
from speak.storage import History, Settings, apply_replacements


def test_legacy_history_upgrades_twice_without_rewriting_original(tmp_path):
    db = sqlite3.connect(tmp_path / "history.sqlite3")
    db.execute("CREATE TABLE results (task_id TEXT PRIMARY KEY, created REAL, text TEXT, provider TEXT, model TEXT, elapsed REAL)")
    db.execute("INSERT INTO results VALUES ('old', ?, '原文', 'cloud', 'test', 2)", (time.time(),))
    db.commit(); db.close()
    history = History(tmp_path)
    History(tmp_path)
    assert history.list()[0][2:7] == ("原文", "cloud", "test", 2, None)
    history.save_edit("old", "修改稿")
    history.add("old", Result("迟到的重复结果", "cloud", "test", 3))
    assert history.list()[0][2] == "原文"
    assert history.list()[0][6] == "修改稿"
    history.save_edit("old", "")
    assert history.list()[0][6] == ""
    history.clear()
    with pytest.raises(ValueError):
        history.save_edit("old", "不能复活已清空记录")


@pytest.mark.parametrize("before,after", [("你好", "你好！"), ("你好", "你好世界"), ("你好世界", "你好")])
def test_insert_delete_and_punctuation_are_not_rules(before, after):
    assert correction_candidates(before, after) == []


def test_candidates_are_editable_suggestions_not_automatic_rules():
    assert correction_candidates("使用 Cloude 和扣德克斯", "使用 Claude 和Codex") == [("Cloude", "Claude"), ("扣德克斯", "Codex")]
    pairs = correction_candidates("堆溢出", "栈溢出")
    assert pairs == [("堆", "栈")]
    with pytest.raises(ValueError, match="完整词语"):
        validate_rule(*pairs[0])
    assert validate_rule("堆溢出", "栈溢出") == ("堆溢出", "栈溢出")
    assert apply_replacements("CAPITable API API2 AA BB", {"API": "接口", "AA": "BB", "BB": "CC"}) == "CAPITable 接口 API2 BB CC"


def test_stale_settings_preserve_new_rules_and_conflicting_decisions(tmp_path):
    settings = Settings(replacements={"旧错词": "旧正确词"})
    baseline = dict(settings.replacements)
    settings.update({"replacements": {**baseline, "新错词": "新正确词"}}, directory=tmp_path)
    settings.update({"mode": "local", "replacements": baseline}, rule_baseline=baseline, directory=tmp_path)
    assert Settings.load(tmp_path).replacements["新错词"] == "新正确词"
    settings.update({"replacements": {"旧错词": "用户新决定", "新错词": "新正确词"}}, directory=tmp_path)
    with pytest.raises(ValueError, match="另一窗口"):
        settings.update({"replacements": {}}, rule_baseline=baseline, directory=tmp_path)
    assert settings.replacements["旧错词"] == "用户新决定"
    assert merge_rule_edits({"AA": "BB"}, {"AA": "BB"}, {}) == {}
    History(tmp_path).clear()
    assert Settings.load(tmp_path).replacements == settings.replacements


def test_cloud_segment_setting_migrates_and_validates(tmp_path):
    (tmp_path / "settings.json").write_text('{"cloud_segmented": "false"}', "utf-8")
    assert Settings.load(tmp_path).cloud_segmented is True
    settings = Settings(cloud_segmented=False)
    settings.save(tmp_path)
    assert Settings.load(tmp_path).cloud_segmented is False
