"""DeepSeek compatibility for gateways that return DSML as plain text."""

from nanobot.providers.openai_compat_provider import _parse_dsml_tool_calls


def test_parse_plain_text_dsml_tool_call() -> None:
    content = (
        "I will inspect the workspace.\n"
        "<｜｜DSML｜｜tool_calls>"
        '<｜｜DSML｜｜invoke name="list_dir">'
        '<｜｜DSML｜｜parameter name="path" string="true">.</｜｜DSML｜｜parameter>'
        '<｜｜DSML｜｜parameter name="recursive">false</｜｜DSML｜｜parameter>'
        "</｜｜DSML｜｜invoke>"
        "</｜｜DSML｜｜tool_calls>"
    )

    cleaned, calls = _parse_dsml_tool_calls(content)

    assert cleaned == "I will inspect the workspace."
    assert len(calls) == 1
    assert calls[0].name == "list_dir"
    assert calls[0].arguments == {"path": ".", "recursive": "false"}
    assert len(calls[0].id) == 9

