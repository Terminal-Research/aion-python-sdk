"""A model gets the files of a conversation by their content, not their address.

A stored file is an address in the graph's state. OpenAI's Chat Completions
takes no file by address, and no provider can be relied on to fetch a
download link of the deployment's storage. ``inline_files`` puts the content
into the request, read from memory or through the turn's conversation, and
leaves the state as it is.
"""

from __future__ import annotations

import base64
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.content import create_file_block, create_image_block, create_text_block
from langchain_openai import ChatOpenAI

from aion.core.a2a.file_contents import file_contents
from aion.core.agent.testing import InMemoryConversation, use_conversation
from aion.core.exceptions import TaskFailedError
from aion.langgraph.authoring import inline_files, inline_files_sync
from aion.langgraph.authoring.models import _file_inlining_chat_openai

PDF_URL = "https://files.test/report?grant=abc"
PNG_URL = "https://files.test/chart?grant=abc"
PDF, PNG = b"%PDF-1.7 report", b"\x89PNG chart"


def _conversation() -> InMemoryConversation:
    return InMemoryConversation(files={PDF_URL: PDF, PNG_URL: PNG})


def _question() -> HumanMessage:
    return HumanMessage(
        content=[
            create_text_block(text="Summarize these."),
            create_file_block(url=PDF_URL, mime_type="application/pdf", filename="report.pdf"),
            create_image_block(url=PNG_URL, mime_type="image/png", filename="chart.png"),
        ]
    )


def _model() -> ChatOpenAI:
    return _file_inlining_chat_openai()(model="gpt-test", api_key="key")


def test_chat_completions_refuses_a_file_given_by_its_address() -> None:
    """Why the content is sent: langchain-core raises on a file URL before any request."""
    with pytest.raises(ValueError, match="does not support file URLs"):
        _model()._get_request_payload([_question()])


async def test_every_file_is_replaced_by_its_content() -> None:
    with use_conversation(_conversation()):
        [message] = await inline_files([_question()])

    text, pdf, png = message.content
    assert text["text"] == "Summarize these."
    assert "url" not in pdf and base64.b64decode(pdf["base64"]) == PDF
    assert "url" not in png and base64.b64decode(png["base64"]) == PNG
    assert (pdf["type"], pdf["mime_type"], pdf["extras"]["filename"]) == ("file", "application/pdf", "report.pdf")
    assert (png["type"], png["mime_type"]) == ("image", "image/png")


async def test_the_request_carries_the_file_and_the_image_as_openai_takes_them() -> None:
    with use_conversation(_conversation()):
        messages = await inline_files([_question()])

    content = _model()._get_request_payload(messages)["messages"][0]["content"]

    assert content[1] == {
        "type": "file",
        "file": {"file_data": f"data:application/pdf;base64,{base64.b64encode(PDF).decode()}", "filename": "report.pdf"},
    }
    assert content[2] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{base64.b64encode(PNG).decode()}"},
    }


async def test_the_messages_passed_in_keep_the_address() -> None:
    """The graph's state is not changed: content goes into the request, never into a checkpoint."""
    question, answer = _question(), AIMessage(content="Done.")

    with use_conversation(_conversation()):
        inlined = await inline_files([question, answer])

    assert question.content[1]["url"] == PDF_URL and "base64" not in question.content[1]
    assert inlined[0] is not question
    assert inlined[1] is answer


async def test_a_file_this_server_holds_in_memory_is_not_read_again() -> None:
    file_contents().put("file:f-1:v-1", PDF, url=PDF_URL)
    conversation = InMemoryConversation()

    with use_conversation(conversation):
        [message] = await inline_files([HumanMessage(content=[create_file_block(url=PDF_URL, mime_type="application/pdf")])])

    assert base64.b64decode(message.content[0]["base64"]) == PDF


async def test_a_file_that_cannot_be_read_fails_the_task_with_its_name() -> None:
    message = HumanMessage(content=[create_file_block(url="https://files.test/gone", mime_type="application/pdf", filename="gone.pdf")])

    with use_conversation(InMemoryConversation()), pytest.raises(TaskFailedError, match="gone.pdf"):
        await inline_files([message])


def test_a_synchronous_call_takes_the_file_from_memory() -> None:
    file_contents().put("file:f-1:v-1", PDF, url=PDF_URL)

    [message] = inline_files_sync([HumanMessage(content=[create_file_block(url=PDF_URL, mime_type="application/pdf")])])

    assert base64.b64decode(message.content[0]["base64"]) == PDF


async def test_the_aion_model_sends_content_on_every_call() -> None:
    """``ainvoke`` and ``astream`` both hand the provider's request the files' content."""
    model = _model()
    requests: list = []

    async def astream(self, messages, stop=None, run_manager=None, **kwargs):
        requests.append(messages)
        raise RuntimeError("stop")
        yield

    with use_conversation(_conversation()):
        with patch.object(ChatOpenAI, "_astream", new=astream):
            with pytest.raises(RuntimeError, match="stop"):
                await model.ainvoke([_question()])

    [[sent]] = requests
    assert base64.b64decode(sent.content[1]["base64"]) == PDF
    assert base64.b64decode(sent.content[2]["base64"]) == PNG


async def test_a_synchronous_node_reads_files_as_an_asynchronous_one_does() -> None:
    """``model.invoke`` in a plain function: LangGraph runs it in a worker thread, and the
    files are read through the turn's conversation all the same - the addresses here lead
    nowhere, so the content can only have come from there."""
    from langgraph.graph import START, MessagesState, StateGraph

    model = _model()
    sent: list = []

    def stream(self, messages, stop=None, run_manager=None, **kwargs):
        sent.extend(messages)
        raise RuntimeError("stop")
        yield

    def answer(state):
        return {"messages": [model.invoke(state["messages"])]}

    builder = StateGraph(MessagesState)
    builder.add_node("answer", answer)
    builder.add_edge(START, "answer")
    graph = builder.compile()

    with use_conversation(_conversation()), patch.object(ChatOpenAI, "_stream", new=stream):
        with pytest.raises(RuntimeError, match="stop"):
            await graph.ainvoke({"messages": [_question()]})

    question = next(message for message in sent if isinstance(message, HumanMessage))
    assert base64.b64decode(question.content[1]["base64"]) == PDF
    assert base64.b64decode(question.content[2]["base64"]) == PNG


class TestACallWithoutAwaitOnTheTurnsEventLoop:
    """``model.invoke`` inside an ``async def`` node runs on the turn's event loop.

    It cannot wait there for a read the loop would make, so a message with a
    file to read fails the task at once, with a reason that says what to call
    instead; nothing is sent. A call without such a file goes to the model,
    and the first one in the process logs a warning: it holds every turn of
    the server until the model answers.
    """

    @staticmethod
    def _graph(model):
        from langgraph.graph import START, MessagesState, StateGraph

        async def answer(state):
            return {"messages": [model.invoke(state["messages"])]}

        builder = StateGraph(MessagesState)
        builder.add_node("answer", answer)
        builder.add_edge(START, "answer")
        return builder.compile()

    async def test_a_message_with_a_file_fails_the_task_at_once(self) -> None:
        sent: list = []

        def stream(self, messages, stop=None, run_manager=None, **kwargs):
            sent.extend(messages)
            raise RuntimeError("stop")
            yield

        # The turn holds the file, as it holds an attachment of its own message: the call fails all the same.
        file_contents().put(f"url:{PDF_URL}", PDF, url=PDF_URL)
        with use_conversation(_conversation()), patch.object(ChatOpenAI, "_stream", new=stream):
            with pytest.raises(TaskFailedError, match=r"Call ainvoke\(\) or astream\(\) in an async node"):
                await self._graph(_model()).ainvoke({"messages": [_question()]})

        assert sent == []

    async def test_inline_files_sync_fails_the_same_way(self) -> None:
        with use_conversation(_conversation()), pytest.raises(TaskFailedError, match="report.pdf"):
            inline_files_sync([_question()])

    async def test_a_message_without_a_file_goes_to_the_model_with_one_warning(self, caplog, monkeypatch) -> None:
        from aion.langgraph.authoring import models

        monkeypatch.setattr(models, "_warned_on_the_turns_event_loop", False)
        sent: list = []

        def stream(self, messages, stop=None, run_manager=None, **kwargs):
            sent.extend(messages)
            raise RuntimeError("stop")
            yield

        with use_conversation(_conversation()), patch.object(ChatOpenAI, "_stream", new=stream):
            with caplog.at_level("WARNING", logger=models.__name__):
                for _ in range(2):
                    with pytest.raises(RuntimeError, match="stop"):
                        await self._graph(_model()).ainvoke({"messages": [HumanMessage(content="Hello")]})

        assert [message.content for message in sent] == ["Hello", "Hello"]
        warnings = [record.getMessage() for record in caplog.records if record.name == models.__name__]
        assert len(warnings) == 1 and "every turn of this server waits" in warnings[0]

    async def test_a_synchronous_node_logs_no_warning(self, caplog, monkeypatch) -> None:
        from langgraph.graph import START, MessagesState, StateGraph

        from aion.langgraph.authoring import models

        monkeypatch.setattr(models, "_warned_on_the_turns_event_loop", False)
        model = _model()

        def stream(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("stop")
            yield

        def answer(state):
            return {"messages": [model.invoke(state["messages"])]}

        builder = StateGraph(MessagesState)
        builder.add_node("answer", answer)
        builder.add_edge(START, "answer")

        with use_conversation(_conversation()), patch.object(ChatOpenAI, "_stream", new=stream):
            with caplog.at_level("WARNING", logger=models.__name__), pytest.raises(RuntimeError, match="stop"):
                await builder.compile().ainvoke({"messages": [HumanMessage(content="Hello")]})

        assert not [record for record in caplog.records if record.name == models.__name__]
