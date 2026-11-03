"""Builds fake OpenRouter responses for tests. No test contacts the real provider."""

import json
import socket
import threading
import time

COMPLETIONS_PATH = "/api/v1/chat/completions"
HTTP_200 = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"


class ScriptedProvider:
    """A real local socket that sends some bytes, then repeats a piece slowly for a while.

    The HTTP stub always replies at once, so it cannot show how a slow server behaves. Sending
    stops by itself, so a client with no working time limit fails its test instead of hanging.
    """

    def __init__(self, first=b"", drip=b"", gap=0.05, sends_for=10):
        self._first = first
        self._drip = drip
        self._gap = gap
        self._sends_for = sends_for
        self._stopping = threading.Event()
        # Set when the client closes the connection, which shows it really stopped reading.
        self.disconnected = threading.Event()
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self._listener.getsockname()[1]}/api/v1"
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                connection, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        with connection:
            try:
                connection.settimeout(5)
                connection.recv(65536)
                connection.sendall(self._first)
                connection.settimeout(self._gap)
                give_up_at = time.monotonic() + self._sends_for
                while not self._stopping.is_set() and time.monotonic() < give_up_at:
                    if self._drip:
                        connection.sendall(self._drip)
                    try:
                        if not connection.recv(65536):
                            self.disconnected.set()
                            return
                    except TimeoutError:
                        pass
            except OSError:
                self.disconnected.set()

    def stop(self):
        self._stopping.set()
        self._listener.close()


def valid_content(**overrides):
    content = {
        "explanation": "The container keeps stopping and Kubernetes keeps starting it again.",
        "impact": "The workload may not be able to serve requests while this continues.",
        "evidence_summary": ["The container has restarted repeatedly."],
        "investigation_steps": ["Read the container logs and describe the pod."],
        "limitations": ["The evidence does not establish why the process exits."],
    }
    content.update(overrides)
    return content


def valid_rca(evidence_ids, **overrides):
    """A root cause analysis that passes every check for the stub crash-loop cluster, citing real evidence."""
    first = evidence_ids[0]
    content = {
        "summary": "A container in this namespace keeps restarting.",
        "root_cause": {
            "statement": "The container process exits soon after it starts, so Kubernetes keeps restarting it.",
            "kind": "hypothesis",
            "evidence_ids": [first],
        },
        "reasoning": ["The container is waiting to be restarted after repeated exits."],
        "supporting_evidence": [{"evidence_id": first, "observation": "The container is not ready and has restarted."}],
        "confidence": "medium",
        "limitations": ["The evidence does not show why the process exits."],
        "alternative_causes": [],
        "remediation": [
            {
                "action": "Read the logs of the previous container run.",
                "why": "They may show why the process exits.",
                "risk": "low",
                "commands": [],
                "evidence_ids": [first],
            }
        ],
        "verification_steps": ["Run a new investigation after the fix."],
    }
    content.update(overrides)
    return content


def valid_chat(cite=(), **overrides):
    """A chat answer that passes every check for the stub crash-loop cluster, citing the first of cite."""
    content = {
        "answer": "The container keeps restarting because its process exits soon after it starts.",
        "scope": "investigation",
        "evidence_ids": list(cite)[:1],
        "remediation": [],
        "limitations": ["The evidence does not show why the process exits."],
        "follow_up_questions": ["What do the previous logs show?"],
    }
    content.update(overrides)
    return content


FREE_TEST_MODEL = "test/model:free"


def completion(content, finish_reason="stop", model=FREE_TEST_MODEL, **message_fields):
    """An OpenRouter chat completion whose message content is the given value, from a free model."""
    text = content if isinstance(content, str) or content is None else json.dumps(content)
    return {
        "id": "gen-test",
        "model": model,
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": text, **message_fields},
            }
        ],
    }


def sequence(*responses):
    """A stub route returning each response in turn, then repeating the last one."""
    remaining = list(responses)

    def route(_query):
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return route
