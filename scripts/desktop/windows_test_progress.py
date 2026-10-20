"""Адресные отметки pytest для диагностики Windows CI без архива журналов.

Подключается только явным -p; исходы тестов и их исключения не изменяет.
"""


def _notice(phase: str, nodeid: str) -> None:
    message = f"{phase}: {nodeid}"
    for original, escaped in (("%", "%25"), ("\r", "%0D"), ("\n", "%0A")):
        message = message.replace(original, escaped)
    print(f"::notice title=Windows source test::{message}", flush=True)


def pytest_runtest_logstart(nodeid: str, location: tuple) -> None:
    _notice("start", nodeid)


def pytest_runtest_logfinish(nodeid: str, location: tuple) -> None:
    _notice("finish", nodeid)
