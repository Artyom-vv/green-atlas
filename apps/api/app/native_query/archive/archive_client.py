"""Native-клиент сохранённого захвата запускает собственные Core-задания без обращения к
процессу редактора.
"""

from collections.abc import Callable
from threading import RLock

import ijson

from app.desktop.tickets import read_regular
from app.native_query.archive.archive_face_process import prepare_archive_faces
from app.native_query.archive.archive_faces import (
    FACE_PROTOCOL,
    LEGACY_FACE_PROTOCOL,
    TOPOLOGY_FACE_PROTOCOL,
    encode_faces,
)
from app.native_query.capture.capture_contracts import (
    ARCHIVED_PLUGIN_VERSIONS,
    compatible_archive_worker,
    decode_instance,
)
from app.native_query.capture.capture_store import (
    NativeCaptureStore,
    _scan_check,
    read_capture_receipt,
)
from app.native_query.contracts import NativeObjectQuery, NativeObjectReply
from app.native_query.live.live_client import LiveSession
from app.native_query.live.live_inventory import LiveInventory, load_inventory
from app.native_query.preparation.face_contracts import FacePreparation, TopologyRecipe
from app.native_query.transport.process import run_native_query
from app.native_query.transport.process_contracts import (
    NativeInputPackage,
    NativeQueryProcessConfig,
)
from app.operations.progress import cancellable_lock, check_operation_cancelled


class ArchiveGeometryClient:
    def __init__(
        self,
        store: NativeCaptureStore,
        project_id: str,
        session: LiveSession,
        config_factory: Callable[[], NativeQueryProcessConfig],
        *,
        inventory: LiveInventory | None = None,
        face_inputs_sha256: str | None = None,
    ) -> None:
        self.store, self.project_id, self.session = store, project_id, session
        self.config_factory = config_factory
        self.layers: tuple[str, ...] = ()
        self.preparation_recipe: TopologyRecipe | None = None
        self._inventory = inventory
        self.face_inputs_sha256 = face_inputs_sha256
        self._inventory_lock = RLock()

    def set_preparation_recipe(self, recipe: TopologyRecipe | None) -> None:
        if recipe is not None and recipe.source_sha256 != self.session.snapshot_sha256:
            raise ValueError("Границы относятся к другому CAD-захвату")
        self.preparation_recipe = recipe

    def inspect(self, session: LiveSession) -> None:
        check_operation_cancelled()
        if (
            session != self.session
            or session.plugin_version not in ARCHIVED_PLUGIN_VERSIONS
        ):
            raise ValueError("CAD-архив не поддерживает подготовку этого захвата")
        retained = self.store.retained_session(self.project_id, session)
        if retained != session:
            raise ValueError("Пути CAD-архива не соответствуют сохранённому проекту")

    def inventory(self, session: LiveSession) -> LiveInventory:
        """Переиспользовать исходный каталог в пределах клиента одного захвата."""
        with cancellable_lock(self._inventory_lock):
            # Модель разделяется с расчётным движком, но не заменяет проверку
            # файлов: изменение физического отпечатка требует прежней SHA-проверки.
            self.inspect(session)
            if self._inventory is None:
                inventory = load_inventory(
                    session, check=_scan_check(check_operation_cancelled)
                )
                self.inspect(session)
                self._inventory = inventory
            return self._inventory

    def inputs(
        self, session: LiveSession, layers: tuple[str, ...]
    ) -> tuple[NativeInputPackage, NativeQueryProcessConfig, bytes]:
        self.inspect(session)
        package = self.store.package(self.project_id, session.snapshot_sha256)
        if package is None:
            raise ValueError("CAD-архив проекта отсутствует")
        config = self.config_factory()
        if not compatible_archive_worker(session.plugin_version, config.plugin_version):
            raise ValueError("Фоновый модуль не поддерживает версию CAD-захвата")
        recipe = self.preparation_recipe
        if recipe is not None and (
            TOPOLOGY_FACE_PROTOCOL not in config.face_protocols
            or not {"curve", "AcDbRegion", "AcDbHatch"} <= set(config.topology_shapes)
        ):
            raise ValueError("Обновите фоновый модуль для межслойной подготовки")
        source = next(item for item in package.files if item.path == package.entry)
        request = encode_faces(
            package.root,
            self.inventory(session),
            session.session_id,
            source.sha256,
            session.units_code,
            layers,
            check_operation_cancelled,
            protocol=TOPOLOGY_FACE_PROTOCOL
            if recipe is not None
            else (
                FACE_PROTOCOL
                if config.plugin_version in {"0.1.47", "0.1.48", "0.1.49"}
                else LEGACY_FACE_PROTOCOL
            ),
            companion_layers=recipe.companion_layers if recipe is not None else (),
            excluded_routes=frozenset(recipe.excluded_routes)
            if recipe is not None
            else frozenset(),
            face_inputs_sha256=self.face_inputs_sha256,
        )
        return package, config, request

    def prepare_faces(
        self, session: LiveSession, layers: tuple[str, ...]
    ) -> FacePreparation:
        # Пересобираем выбранный каталог полностью: прежний выбор слоёв не переносит
        # идентификаторы областей и незавершённую работу в новое задание.
        package, config, request = self.inputs(session, layers)
        reply = prepare_archive_faces(
            package,
            request,
            session.session_id,
            layers,
            config,
            companion_layers=self.preparation_recipe.companion_layers
            if self.preparation_recipe is not None
            else (),
        )
        self.inspect(session)
        self.layers = layers
        return reply

    def measure(
        self, session: LiveSession, query: NativeObjectQuery
    ) -> NativeObjectReply:
        if (
            query.source_sha256 != session.source_sha256
            or query.units_code != session.units_code
        ):
            raise ValueError("Проверка относится к другому исходнику AutoCAD")
        package, config, face_request = self.inputs(session, self.layers)
        needed = {
            route
            for target in query.targets
            if not target.face_id
            for route in (target.route, *target.additional_routes)
        }
        aliases = {}
        receipt = read_capture_receipt(package.root)
        with read_regular(package.root / "instances.json") as stream:
            for value in ijson.items(stream, "item", use_float=True):
                check_operation_cancelled()
                row = decode_instance(value, receipt.schema_)
                if row.source_route in needed and not row.unavailable:
                    aliases[row.source_route] = row.archive_route
        if set(aliases) != needed:
            raise ValueError("Проверяемый объект отсутствует в CAD-архиве")
        targets = tuple(
            target
            if target.face_id
            else target.model_copy(
                update={
                    "route": aliases[target.route],
                    "additional_routes": tuple(
                        aliases[route] for route in target.additional_routes
                    ),
                }
            )
            for target in query.targets
        )
        source = next(item for item in package.files if item.path == package.entry)
        archived = NativeObjectQuery(
            request_id=query.request_id,
            source_sha256=source.sha256,
            units_code=query.units_code,
            targets=targets,
            points=query.points,
        )

        def cancelled() -> bool:
            check_operation_cancelled()
            return False

        result = run_native_query(
            package,
            archived,
            config,
            cancelled=cancelled,
            face_request=face_request if any(t.face_id for t in targets) else None,
        )
        self.inspect(session)
        if result.reply is None or result.failure is not None:
            raise ValueError(
                "Фоновая проверка AutoCAD не завершена. Повторите действие"
            )
        reply = result.reply
        objects = tuple(
            item.model_copy(
                update={
                    "route": target.route,
                    "additional_routes": target.additional_routes,
                }
            )
            for item, target in zip(reply.objects, query.targets, strict=True)
        )
        return reply.model_copy(
            update={"source_sha256": query.source_sha256, "objects": objects}
        )
