import json
import logging

from app.core.logging import JsonFormatter


def test_json_formatter_includes_extra():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("x",), None)
    record.request_id = "abc"
    data = json.loads(JsonFormatter().format(record))
    assert data["message"] == "hello x"
    assert data["level"] == "INFO"
    assert data["request_id"] == "abc"
