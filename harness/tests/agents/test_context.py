

def test_compaction_stubs_the_file_bodies_the_model_already_wrote():
    """Measured 2026-08-27 (fancy_bl_windsor_chair assemble, 60 turns): 29 write_file
    calls were 49 % of a conversation that reached 113 956 input tokens, and every later
    turn re-processed all of it.  The file is on disk; read_file can fetch it."""
    from codeverse.agents.context import compact_messages, message_chars
    from codeverse.contracts.chat import ChatMessage, ToolCallPart

    body = "import bpy\n" * 400
    msgs = [ChatMessage.user("build a chair")]
    for i in range(10):
        msgs.append(ChatMessage(role="assistant", parts=[
            ToolCallPart(id=f"c{i}", name="write_file",
                         arguments={"path": f"src/parts/p{i}.py", "content": body})]))
        msgs.append(ChatMessage.user(f"wrote p{i}"))
    before = message_chars(msgs)
    after = message_chars(compact_messages(msgs, keep_recent=4))
    assert after < before / 2, f"{before} -> {after}: the written bodies must not survive"
    kept = compact_messages(msgs, keep_recent=4)
    assert kept[0].text == "build a chair"      # the task is never touched
    tail_call = next(p for p in kept[-2].parts if isinstance(p, ToolCallPart))
    assert tail_call.arguments["content"] == body, "recent turns stay verbatim"
