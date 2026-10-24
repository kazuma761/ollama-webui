"""Checks of the pipeline's own logic, without a model or a server:  cd backend && python -m ollama_pipeline.selfcheck

(`python -m ollama_pipeline` checks the model servers instead.)
"""

from .context import MAX_ANSWER, budget_for, estimate, fit, shrink, total_tokens
def main() -> None:
    assert estimate("hello world") < estimate("1234567890 1234567890") < estimate("ಮಾನವ ಸಂಪನ್ಮೂಲ ಇಲಾಖೆ")
    budget = budget_for(8192)
    assert budget.reserve == 1638 and budget.prompt == 8192 - 1638
    assert budget_for(None) is None and budget_for(65536).reserve == MAX_ANSWER

    long = "word " * 4000  # about 1,200 tokens
    chat = [{"role": "system", "content": "Be brief."}]
    for i in range(12):
        chat += [{"role": "user", "content": f"question {i} " + long}, {"role": "assistant", "content": f"answer {i} " + long}]
    chat.append({"role": "user", "content": "the question now"})
    fitted = fit(chat, 6000)
    assert total_tokens(fitted.messages) <= 6000, total_tokens(fitted.messages)
    assert fitted.messages[0]["content"] == "Be brief." and fitted.messages[-1]["content"] == "the question now"
    assert fitted.messages[1]["role"] == "user" and fitted.dropped > 10, (fitted.dropped, fitted.messages[1]["role"])
    assert chat[1]["content"].startswith("question 0") and len(chat) == 26  # the input is left as it was
    assert "oldest" in fitted.notice(budget)

    # The current turn is kept whole even with its tool rounds; an older tool result is shortened.
    turn = chat[:1] + [{"role": "user", "content": "how many orders?"},
                       {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "query", "arguments": {}}}]},
                       {"role": "tool", "content": "row " * 5000},
                       {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "query", "arguments": {}}}]},
                       {"role": "tool", "content": "[[42]]"},
                       {"role": "user", "content": "Stop calling tools now and answer with what you found."}]
    fitted = fit(turn, 1500, keep_from=1)
    assert [m["role"] for m in fitted.messages] == [m["role"] for m in turn] and fitted.messages[5]["content"] == "[[42]]"
    assert total_tokens(fitted.messages) <= 1500

    # A question too big alone keeps its start and its end.
    huge = [{"role": "user", "content": "START " + "x " * 40000 + " END: what is the total?"}]
    fitted = fit(huge, 2000)
    assert fitted.messages[0]["content"].startswith("START") and fitted.messages[0]["content"].endswith("what is the total?")
    assert total_tokens(fitted.messages) <= 2000 and fitted.shortened == 1

    # After a refusal, the server's own counts make the next try fit.
    smaller = shrink(budget, fitted.messages, prompt_tokens=9000, context=8192)
    assert smaller.context == 8192 and smaller.prompt < budget.prompt
    print("Pipeline self-check - context: estimates, budgets, fitting, the current turn kept, cutting the middle and retrying all pass.")


if __name__ == "__main__":
    main()
