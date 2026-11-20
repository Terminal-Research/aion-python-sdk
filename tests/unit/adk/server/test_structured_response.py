"""The object an ADK ``LlmAgent`` with an ``output_schema`` answers with, as the server reads it.

The final response of such an agent is the model's JSON of an object of the
schema. The server reads it the way ADK does - the value ADK put under the
agent's ``output_key``, else the text by the schema - and sends the turn's last
one as the ``structured_response`` artifact, unless an outbox answers the turn.
"""

from __future__ import annotations

import logging

import pytest
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.events import Event, EventActions
from google.genai import types
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel

from aion.adk.server.execution.result_handler import ADKExecutionResultHandler
from aion.adk.server.execution.stream_executor import ADKStreamResult
from aion.adk.server.execution.structured_response import NO_STRUCTURED_RESPONSE, structured_response


class Forecast(BaseModel):
    city: str
    degrees: int
    note: str | None = None


def _agent(**kwargs) -> LlmAgent:
    return LlmAgent(name="forecaster", model="gemini-2.0-flash", **kwargs)


def _final(text: str, *, author: str = "forecaster", partial: bool = False, state_delta: dict | None = None) -> Event:
    return Event(
        author=author,
        partial=partial,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        actions=EventActions(state_delta=state_delta or {}),
    )


class TestTheObject:
    def test_the_final_text_is_read_by_the_schema(self):
        root = _agent(output_schema=Forecast)

        assert structured_response(_final('{"city": "Kyiv", "degrees": 5}'), root) == {"city": "Kyiv", "degrees": 5}

    def test_the_value_adk_kept_under_the_output_key_is_the_object(self):
        root = _agent(output_schema=Forecast, output_key="forecast")
        event = _final('{"city": "Kyiv", "degrees": 5}', state_delta={"forecast": {"city": "Kyiv", "degrees": 5}})

        assert structured_response(event, root) == {"city": "Kyiv", "degrees": 5}

    def test_a_fenced_json_answer_is_read_as_adk_reads_it(self):
        root = _agent(output_schema=Forecast)

        assert structured_response(_final('```json\n{"city": "Kyiv", "degrees": 5}\n```'), root)["city"] == "Kyiv"

    def test_a_schema_that_is_no_model_is_validated_by_pydantic(self):
        root = _agent(output_schema=list[Forecast])

        answer = structured_response(_final('[{"city": "Kyiv", "degrees": 5}]'), root)

        assert answer == [{"city": "Kyiv", "degrees": 5}]

    def test_a_json_schema_is_read_as_json(self):
        root = _agent(output_schema={"type": "object", "properties": {"city": {"type": "string"}}})

        assert structured_response(_final('{"city": "Kyiv"}'), root) == {"city": "Kyiv"}

    def test_a_step_of_a_sequence_is_read_by_its_own_schema(self):
        root = SequentialAgent(name="seq", sub_agents=[_agent(output_schema=Forecast)])

        assert structured_response(_final('{"city": "Kyiv", "degrees": 5}'), root)["degrees"] == 5


class TestNoObject:
    def test_an_agent_without_a_schema_answers_with_none(self):
        assert structured_response(_final('{"city": "Kyiv", "degrees": 5}'), _agent()) is NO_STRUCTURED_RESPONSE

    def test_a_chunk_of_the_answer_is_not_it(self):
        root = _agent(output_schema=Forecast)

        assert structured_response(_final('{"city"', partial=True), root) is NO_STRUCTURED_RESPONSE

    def test_a_text_that_does_not_match_the_schema_stays_text(self, caplog):
        root = _agent(output_schema=Forecast)

        with caplog.at_level(logging.WARNING):
            answer = structured_response(_final("It is sunny in Kyiv."), root)

        assert answer is NO_STRUCTURED_RESPONSE
        assert "does not match its output_schema" in caplog.text


class TestTheArtifact:
    def test_the_object_goes_out_as_the_structured_response_artifact(self):
        result = ADKStreamResult(structured_response={"city": "Kyiv", "degrees": 5})

        [event] = ADKExecutionResultHandler().handle(result, converter=None, task_id="t-1", context_id="c-1")

        assert event.artifact.name == "structured_response"
        assert [MessageToDict(part.data) for part in event.artifact.parts] == [{"city": "Kyiv", "degrees": 5}]

    def test_an_outbox_answers_the_turn_instead(self, caplog):
        outbox = {"message": {"role": "ROLE_AGENT", "parts": [{"text": "Done."}], "messageId": "m-1"}}
        result = ADKStreamResult(outbox=outbox, structured_response={"city": "Kyiv", "degrees": 5})

        with caplog.at_level(logging.INFO):
            events = ADKExecutionResultHandler().handle(result, converter=None, task_id="t-1", context_id="c-1")

        assert all(getattr(event, "artifact", None) is None or event.artifact.name != "structured_response" for event in events)
        assert "structured response is not sent" in caplog.text
