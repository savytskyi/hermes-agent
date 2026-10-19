"""User ``quick_commands`` appear in Telegram's slash-command menu (setMyCommands payload)."""

from unittest.mock import patch

from hermes_cli.commands_platforms import (
    _configured_quick_telegram_commands,
    telegram_menu_commands,
)

_QUICK = {
    "quick_commands": {
        "run_leads": {"type": "exec", "command": "echo leads", "description": "Run the lead monitor now"},
        "run-elektro": {"type": "exec", "command": "echo e", "timeout_seconds": 120},
        "go_model": {"type": "alias", "target": "/model"},
        "broken": {"type": "nope", "command": "echo"},
        "not_a_mapping": "echo",
        "help": {"type": "exec", "command": "echo shadow", "description": "Shadows a built-in"},
    }
}


def _menu(raw_config, *, core=None, max_commands=100, menu_cfg=None, skills=None, tmp_path=None):
    patches = [
        patch("hermes_cli.config.read_raw_config", return_value=raw_config),
        patch("agent.skill_commands.get_skill_commands", return_value=skills or {}),
        patch("hermes_cli.plugins.get_plugin_commands", return_value={}),
    ]
    if core is not None:
        patches.append(patch("hermes_cli.commands_platforms.telegram_bot_commands", return_value=core))
    if menu_cfg is not None:
        patches.append(patch("hermes_cli.commands_platforms._telegram_command_menu_config",
                             return_value=menu_cfg))
    if tmp_path is not None:
        patches.append(patch("tools.skills_tool.SKILLS_DIR", tmp_path))
    for p in patches:
        p.start()
    try:
        return telegram_menu_commands(max_commands=max_commands)
    finally:
        for p in reversed(patches):
            p.stop()


def test_configured_quick_commands_are_sanitized_and_filtered():
    with patch("hermes_cli.config.read_raw_config", return_value=_QUICK):
        entries = dict(_configured_quick_telegram_commands())
    assert entries["run_leads"] == "Run the lead monitor now"
    assert entries["run_elektro"] == "Run /run_elektro"  # hyphen sanitized; default description
    assert entries["go_model"] == "Run /go_model"  # alias quick commands are listed too
    assert "broken" not in entries and "not_a_mapping" not in entries


def test_no_quick_commands_or_bad_config_yields_nothing():
    for raw in ({}, {"quick_commands": "oops"}, None):
        with patch("hermes_cli.config.read_raw_config", return_value=raw):
            assert _configured_quick_telegram_commands() == []
    with patch("hermes_cli.config.read_raw_config", side_effect=RuntimeError("boom")):
        assert _configured_quick_telegram_commands() == []


def test_quick_commands_in_menu_and_builtins_win_name_clashes(tmp_path):
    menu, _hidden = _menu(_QUICK, tmp_path=tmp_path)
    names = [n for n, _ in menu]
    assert {"run_leads", "run_elektro", "go_model"} <= set(names)
    assert names.count("help") == 1
    assert dict(menu)["help"] != "Shadows a built-in"


def test_quick_commands_survive_small_cap_ahead_of_lower_priority_builtins(tmp_path):
    core = [("help", "Help"), ("new", "New"), ("commands", "Commands"),
            ("reasoning", "Reasoning"), ("zzz_unranked", "Unranked")]
    menu_cfg = {"max_commands": 5, "priority_mode": "prepend", "priority": []}
    raw = {"quick_commands": {"run_x_sentiment": {"type": "exec", "command": "echo"},
                              "run_cinema_search": {"type": "exec", "command": "echo"}}}
    menu, hidden = _menu(raw, core=core, max_commands=5, menu_cfg=menu_cfg, tmp_path=tmp_path)
    names = [n for n, _ in menu]
    assert names == ["help", "new", "commands", "run_x_sentiment", "run_cinema_search"]
    assert hidden == 2


def test_configured_priority_still_overrides_quick_default_rank(tmp_path):
    core = [("help", "Help"), ("commands", "Commands")]
    menu_cfg = {"max_commands": 3, "priority_mode": "prepend", "priority": ["run_b"]}
    raw = {"quick_commands": {"run_a": {"type": "exec", "command": "echo"},
                              "run_b": {"type": "exec", "command": "echo"}}}
    menu, _ = _menu(raw, core=core, max_commands=3, menu_cfg=menu_cfg, tmp_path=tmp_path)
    assert [n for n, _ in menu] == ["run_b", "help", "commands"]


def test_skill_with_same_name_as_quick_command_does_not_duplicate(tmp_path):
    skills = {"/run-leads": {"name": "run-leads", "description": "Skill",
                             "skill_md_path": f"{tmp_path}/run-leads/SKILL.md",
                             "skill_dir": f"{tmp_path}/run-leads"}}
    raw = {"quick_commands": {"run_leads": {"type": "exec", "command": "echo",
                                            "description": "Quick"}}}
    menu, _ = _menu(raw, core=[("help", "Help")], skills=skills, tmp_path=tmp_path)
    names = [n for n, _ in menu]
    assert names.count("run_leads") == 1
    assert dict(menu)["run_leads"] == "Quick"
