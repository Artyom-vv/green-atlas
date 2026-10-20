"""Постоянная идентичность native-захвата и полнота его файлов; отображаемые данные не
являются проверяющим модулем.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from app.native_query.contracts import Route, Sha256

CadName = Annotated[str, Field(pattern=r"^(host|xref-[1-9][0-9]*)\.dwg$")]
MAX_SESSION_BYTES = 8 * 1024**3
# Полный захват с загруженными подосновами содержит более миллиона экземпляров.
# Байтовые бюджеты согласованы с изолированным импортом под надзором 6 ГиБ.
MAX_CAPTURE_OBJECTS = 2_000_000
MAX_CAPTURE_DISPLAY_BYTES = 2 * 1024**3
MAX_CAPTURE_INVENTORY_BYTES = 1024**3
MAX_CAPTURE_INSTANCES_BYTES = 1024**3
MAX_INVENTORY_RECORD_BYTES = 16 * 1024**2
MAX_CAPTURE_RECEIPT_BYTES = 4 * 1024**2
MAX_SEMANTICS_BYTES = 32 * 1024**2
MAX_SEMANTIC_ROWS = 100_000
NativeSessionSchema = Literal[
    "green-atlas.native-session/1",
    "green-atlas.native-session/2",
    "green-atlas.native-session/3",
]
ARCHIVED_PLUGIN_VERSIONS = frozenset({"0.1.46", "0.1.47", "0.1.48"})
CAPTURE_SCHEMA_BY_PLUGIN: dict[str, NativeSessionSchema] = {
    "0.1.39": "green-atlas.native-session/1",
    "0.1.40": "green-atlas.native-session/1",
    "0.1.41": "green-atlas.native-session/1",
    "0.1.42": "green-atlas.native-session/1",
    "0.1.43": "green-atlas.native-session/1",
    "0.1.44": "green-atlas.native-session/1",
    "0.1.45": "green-atlas.native-session/1",
    "0.1.46": "green-atlas.native-session/1",
    "0.1.47": "green-atlas.native-session/2",
    "0.1.48": "green-atlas.native-session/3",
}


def compatible_archive_worker(capture_version: str, worker_version: str) -> bool:
    """Явные совместимые пары контрактов; больший номер версии не доказывает
    совместимость.
    """
    return (capture_version, worker_version) in {
        ("0.1.46", "0.1.46"),
        ("0.1.46", "0.1.47"),
        ("0.1.47", "0.1.47"),
        # Работник /48 сохраняет протоколы областей /2 и выпуска /47;
        # новые смысловые метаданные имеют отдельный контракт захвата.
        ("0.1.47", "0.1.48"),
        ("0.1.48", "0.1.48"),
        # Работник /49 сохраняет /2 и добавляет явно выбранные native-границы /3.
        ("0.1.47", "0.1.49"),
        ("0.1.48", "0.1.49"),
    }


class CaptureDto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InstanceIdentity(CaptureDto):
    source_route: Route
    archive_route: Route
    class_: str = Field(alias="class", min_length=1)
    unavailable: str
    transform: tuple[
        tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat],
        tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat],
        tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat],
        tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat],
    ]


class InstanceMapping(InstanceIdentity):
    """Session /1 recorded the native authored layer, without an effective one."""

    layer: str = Field(min_length=1)

    @property
    def authored_layer(self) -> str:
        return self.layer


class LayeredInstanceMapping(InstanceIdentity):
    """Session /2 keeps native identity separate from calculation grouping."""

    authored_layer: str = Field(min_length=1)
    effective_layer: str = Field(min_length=1)


class SemanticInstanceMapping(LayeredInstanceMapping):
    """Session /3 binds source evidence to the exact captured database."""

    archive_file: CadName


CapturedInstance = InstanceMapping | LayeredInstanceMapping | SemanticInstanceMapping


def decode_instance(value: Any, schema: NativeSessionSchema) -> CapturedInstance:
    # Формат строки задаёт квитанция. Вывод по наличию полей мог бы изменить
    # смысл старого захвата или допустить неверсионированную семантику.
    if schema == "green-atlas.native-session/1":
        return InstanceMapping.model_validate(value)
    if schema == "green-atlas.native-session/3":
        return SemanticInstanceMapping.model_validate(value)
    return LayeredInstanceMapping.model_validate(value)


class CaptureFile(CaptureDto):
    path: CadName
    sha256: Sha256
    bytes: int = Field(gt=0, le=MAX_SESSION_BYTES)


class CaptureReference(CaptureDto):
    owner: CadName
    archive_record: Annotated[str, Field(pattern=r"^[0-9A-F]{1,16}$")]
    name: str = Field(max_length=4096)
    authored: str = Field(max_length=32768)
    source_status: int = Field(ge=0, le=6)
    source_unloaded: bool
    available: bool
    archive_path: str = Field(
        pattern=r"^(host|xref-[1-9][0-9]*|not-captured-(host|xref-[1-9][0-9]*)\.dwg-[0-9A-F]{1,16})\.dwg$"
    )


class CaptureReceipt(CaptureDto):
    schema_: NativeSessionSchema = Field(alias="schema")
    plugin_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    entry: Literal["host.dwg"]
    units_code: int = Field(ge=0, le=24)
    source_path: str = Field(min_length=1, max_length=32768)
    display_sha256: Sha256
    source_dbmod_before: int = Field(ge=0)
    source_dbmod_after: int = Field(ge=0)
    source_disk_hashes_verified: Literal[True]
    instance_count: int = Field(ge=0)
    instances_sha256: Sha256
    semantics_sha256: Sha256 | None = None
    semantics_bytes: int | None = Field(default=None, gt=0, le=MAX_SEMANTICS_BYTES)
    semantics_count: int | None = Field(default=None, ge=0, le=MAX_SEMANTIC_ROWS)
    files: tuple[CaptureFile, ...] = Field(min_length=1, max_length=4096)
    references: tuple[CaptureReference, ...] = Field(max_length=32768)

    @model_validator(mode="after")
    def coherent_graph(self) -> "CaptureReceipt":
        if CAPTURE_SCHEMA_BY_PLUGIN.get(self.plugin_version) != self.schema_:
            raise ValueError(
                "Native capture schema and plugin version are incompatible"
            )
        semantic_fields = (
            self.semantics_sha256,
            self.semantics_bytes,
            self.semantics_count,
        )
        if (self.schema_ == "green-atlas.native-session/3") != all(
            field is not None for field in semantic_fields
        ) or (
            self.schema_ != "green-atlas.native-session/3"
            and any(field is not None for field in semantic_fields)
        ):
            raise ValueError("Native semantics require their explicit session version")
        names = {entry.path for entry in self.files}
        if len(names) != len(self.files) or self.entry not in names:
            raise ValueError("Native catalogue has duplicate files or no entry")
        if sum(entry.bytes for entry in self.files) > MAX_SESSION_BYTES:
            raise ValueError("Native catalogue exceeds transfer budget")
        edges = {(entry.owner, entry.archive_record) for entry in self.references}
        if len(edges) != len(self.references):
            raise ValueError("Duplicate native XREF record")
        for edge in self.references:
            if edge.owner not in names or edge.available != (
                edge.archive_path in names
            ):
                raise ValueError(
                    "Native XREF catalogue disagrees with captured availability"
                )
            if edge.available and (edge.source_unloaded or edge.source_status != 1):
                raise ValueError(
                    "Unloaded/unresolved XREF cannot be a captured loaded database"
                )
        return self


class SessionTransferFile(CaptureDto):
    name: str = Field(
        pattern=r"^(Native/(session\.json|instances\.json|semantics\.json|(host|xref-[1-9][0-9]*)\.dwg)|Drawing\.autocad\.json(\.inventory\.json)?)$"
    )
    sha256: Sha256
    bytes: int = Field(gt=0, le=MAX_SESSION_BYTES)


class SessionTransfer(CaptureDto):
    entry: Literal["Native/session.json"]
    files: tuple[SessionTransferFile, ...] = Field(min_length=3, max_length=4101)

    @model_validator(mode="after")
    def complete_catalogue(self) -> "SessionTransfer":
        names = {entry.name for entry in self.files}
        if len(names) != len(self.files) or not {
            "Native/session.json",
            "Native/instances.json",
            "Native/host.dwg",
        }.issubset(names):
            raise ValueError("Incomplete or duplicate native capture transfer")
        if sum(entry.bytes for entry in self.files) > MAX_SESSION_BYTES:
            raise ValueError("Native transfer exceeds budget")
        auxiliary = {"Drawing.autocad.json", "Drawing.autocad.json.inventory.json"}
        if names & auxiliary and not auxiliary.issubset(names):
            raise ValueError("Incomplete captured display and inventory")
        limits = {
            "Native/session.json": MAX_CAPTURE_RECEIPT_BYTES,
            "Native/instances.json": MAX_CAPTURE_INSTANCES_BYTES,
            "Native/semantics.json": MAX_SEMANTICS_BYTES,
            "Drawing.autocad.json": MAX_CAPTURE_DISPLAY_BYTES,
            "Drawing.autocad.json.inventory.json": MAX_CAPTURE_INVENTORY_BYTES,
        }
        if any(
            entry.bytes > limits.get(entry.name, MAX_SESSION_BYTES)
            for entry in self.files
        ):
            raise ValueError("Native receipt exceeds budget")
        return self
