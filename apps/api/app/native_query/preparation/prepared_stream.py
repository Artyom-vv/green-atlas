"""Ограниченное чтение прежнего SHA-конверта расчётного снимка.

Строковый payload нельзя передать обычному потоковому JSON-парсеру: одна
строка всё равно занимает память целиком. Здесь стандартный JSON-декодер
разбирает ограниченные части строки, а временный UTF-8 файл принадлежит
только построению производного ресурса. Формат исходника не меняется.
"""

import io
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO, cast

from app.desktop.tickets import direct_path, read_regular
from app.native_query.preparation.prepared_snapshot import FileFingerprint
from app.operations.progress import check_operation_cancelled

_BLOCK = 64 * 1024
_TOKEN_LIMIT = 64 * 1024
_CLOSING_QUOTE = re.compile(r'(?<!\\)(?:\\\\)*"')
_WHITESPACE = re.compile(r"[ \t\r\n]*")
_TOKEN_END = re.compile(r"[ \t\r\n,\]}]")


def _stat_fingerprint(stat: os.stat_result) -> FileFingerprint:
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


def _stream_fingerprint(stream: BinaryIO) -> FileFingerprint:
    if os.name == "nt":
        from app.native_query.transport.windows_job_files import stream_fingerprint

        return stream_fingerprint(stream)
    return _stat_fingerprint(os.fstat(stream.fileno()))


def _path_fingerprint(path: Path) -> FileFingerprint:
    if os.name == "nt":
        from app.native_query.transport.windows_job_files import file_fingerprint

        return file_fingerprint(path)
    return _stat_fingerprint(path.stat())


class _EnvelopeReader:
    def __init__(self, stream: BinaryIO) -> None:
        import codecs

        self.stream = stream
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.buffer = ""
        self.position = 0
        self.eof = False
        self.digest = sha256()

    def fill(self) -> bool:
        if self.eof:
            return False
        check_operation_cancelled()
        chunk = self.stream.read(_BLOCK)
        self.digest.update(chunk)
        self.buffer = self.buffer[self.position :] + self.decoder.decode(
            chunk, final=not chunk
        )
        self.position = 0
        self.eof = not chunk
        return bool(chunk)

    def peek(self) -> str:
        while self.position == len(self.buffer) and self.fill():
            pass
        return self.buffer[self.position : self.position + 1]

    def whitespace(self) -> None:
        while True:
            self.peek()
            matched = _WHITESPACE.match(self.buffer, self.position)
            assert matched is not None
            self.position = matched.end()
            if self.position < len(self.buffer) or not self.fill():
                return

    def expect(self, token: str) -> None:
        self.whitespace()
        if self.peek() != token:
            raise ValueError("Некорректный конверт подготовленной геометрии")
        self.position += 1

    @staticmethod
    def _safe_cut(value: str, start: int, end: int) -> int:
        """Не делить escape-последовательность между вызовами json.loads."""
        slash = value.rfind("\\", max(start, end - 6), end)
        if slash < start:
            return end
        preceding = slash
        while preceding > start and value[preceding - 1] == "\\":
            preceding -= 1
        if (slash - preceding + 1) % 2 == 0:
            return end
        if slash == end - 1 or value[slash + 1] == "u" and end - slash < 6:
            return slash
        return end

    def string(self, write: Callable[[bytes], object]) -> None:
        self.expect('"')
        high_surrogate = ""
        while True:
            if self.position == len(self.buffer) and not self.fill():
                raise ValueError("Незавершённая строка расчётного снимка")
            closing = _CLOSING_QUOTE.search(self.buffer, self.position)
            end = closing.end() - 1 if closing is not None else len(self.buffer)
            if closing is None and not self.eof:
                end = self._safe_cut(self.buffer, self.position, end)
            if end == self.position and closing is None:
                if not self.fill():
                    raise ValueError("Незавершённая строка расчётного снимка")
                continue
            decoded = json.loads('"' + self.buffer[self.position : end] + '"')
            self.position = end
            if high_surrogate:
                if not decoded or not 0xDC00 <= ord(decoded[0]) <= 0xDFFF:
                    raise ValueError("Некорректная Unicode-пара расчётного снимка")
                decoded = (
                    chr(
                        0x10000
                        + ((ord(high_surrogate) - 0xD800) << 10)
                        + ord(decoded[0])
                        - 0xDC00
                    )
                    + decoded[1:]
                )
                high_surrogate = ""
            if decoded and 0xD800 <= ord(decoded[-1]) <= 0xDBFF:
                high_surrogate, decoded = decoded[-1], decoded[:-1]
            if decoded:
                write(decoded.encode("utf-8"))
            if closing is not None:
                if high_surrogate:
                    raise ValueError("Незавершённая Unicode-пара расчётного снимка")
                self.position += 1
                return
            if not self.fill():
                raise ValueError("Незавершённая строка расчётного снимка")

    def text(self, limit: int = _TOKEN_LIMIT) -> str:
        output = io.BytesIO()

        def write(chunk: bytes) -> None:
            if output.tell() + len(chunk) > limit:
                raise ValueError("Слишком большое поле конверта расчётного снимка")
            output.write(chunk)

        self.string(write)
        return output.getvalue().decode("utf-8")

    def skip(self, depth: int = 0) -> None:
        """Проверять неизвестные поля конверта, не удерживая их значения."""
        if depth > 128:
            raise ValueError("Слишком глубокий конверт расчётного снимка")
        self.whitespace()
        kind = self.peek()
        if kind == '"':
            self.string(lambda _: None)
        elif kind in {"{", "["}:
            closing_token = "}" if kind == "{" else "]"
            self.expect(kind)
            self.whitespace()
            if self.peek() == closing_token:
                self.expect(closing_token)
                return
            while True:
                if kind == "{":
                    self.string(lambda _: None)
                    self.expect(":")
                self.skip(depth + 1)
                self.whitespace()
                if self.peek() == closing_token:
                    self.expect(closing_token)
                    return
                self.expect(",")
        else:
            parts: list[str] = []
            size = 0
            while True:
                match = _TOKEN_END.search(self.buffer, self.position)
                end = match.start() if match else len(self.buffer)
                parts.append(self.buffer[self.position : end])
                size += end - self.position
                if size > _TOKEN_LIMIT:
                    raise ValueError("Слишком большое значение конверта")
                self.position = end
                if match or not self.fill():
                    break
            token = "".join(parts)
            value = json.loads(token, parse_constant=_reject_constant)
            if isinstance(value, (list, dict, str)):
                raise ValueError("Некорректное значение конверта")


def _reject_constant(value: str) -> None:
    raise ValueError(f"Некорректная JSON-константа конверта: {value}")


@dataclass(frozen=True)
class PreparedPayload:
    stream: BinaryIO
    source_sha256: str
    payload_sha256: str
    fingerprint: FileFingerprint
    assert_current: Callable[[], None]


@contextmanager
def open_prepared_payload(path: Path, root: Path) -> Iterator[PreparedPayload]:
    """Проверить весь исходный конверт и дать временный seekable UTF-8 поток.

    SHA относится к исходным байтам и отдельно к распакованной строке payload.
    Дескриптор исходника удерживается до завершения построения; замена пути,
    изменение размера или метаданных отвергают результат до публикации.
    """
    path = direct_path(path, root)
    with read_regular(path) as source:
        fingerprint = _stream_fingerprint(source)

        def assert_current() -> None:
            check_operation_cancelled()
            if (
                fingerprint != _stream_fingerprint(source)
                or fingerprint != _path_fingerprint(path)
                or direct_path(path, root) != path
            ):
                raise ValueError("Расчётный снимок изменён во время чтения")

        reader = _EnvelopeReader(source)
        with NamedTemporaryFile(
            mode="w+b", dir=root, prefix=".prepared-payload-", suffix=".pending"
        ) as output:
            payload_digest = sha256()
            has_payload = False
            expected_digest: str | None = None
            reader.expect("{")
            reader.whitespace()
            if reader.peek() != "}":
                while True:
                    key = reader.text()
                    reader.expect(":")
                    reader.whitespace()
                    if key == "payload":
                        has_payload = reader.peek() == '"'
                        output.seek(0)
                        output.truncate(0)
                        payload_digest = sha256()
                        if has_payload:

                            def write(chunk: bytes) -> None:
                                payload_digest.update(chunk)
                                output.write(chunk)

                            reader.string(write)
                        else:
                            reader.skip()
                    elif key == "sha256":
                        expected_digest = (
                            reader.text(64) if reader.peek() == '"' else None
                        )
                        if expected_digest is None:
                            reader.skip()
                    else:
                        reader.skip()
                    reader.whitespace()
                    if reader.peek() == "}":
                        break
                    reader.expect(",")
            reader.expect("}")
            reader.whitespace()
            if reader.peek() or not has_payload or expected_digest is None:
                raise ValueError("Некорректный конверт подготовленной геометрии")
            if payload_digest.hexdigest() != expected_digest:
                raise ValueError("Контрольная сумма расчётного снимка не совпадает")
            assert_current()
            output.flush()
            output.seek(0)
            yield PreparedPayload(
                cast(BinaryIO, output),
                reader.digest.hexdigest(),
                payload_digest.hexdigest(),
                fingerprint,
                assert_current,
            )
            assert_current()
