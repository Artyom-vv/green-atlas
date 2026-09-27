"""Opt-in pairing boundary; no unauthenticated file upload or generic API token."""

import asyncio
import hmac
import os
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from starlette.concurrency import run_in_threadpool

from app.cad_delivery.contracts import (
    PublicationReceipt,
    PublicationRequest,
    TransferCreate,
    TransferDecision,
    TransferReceipt,
    TransferReview,
    TransferStatus,
    UploadProgress,
)
from app.cad_delivery.publication import TransferPublication
from app.cad_delivery.store import TransferError, TransferStore
from app.cad_delivery.uploads import CHUNK_BYTES, UploadStore
from app.composition import Runtime, get_cad_intake, get_runtime

router = APIRouter(prefix="/api/cad-bridge", tags=["AutoCAD delivery"])


def public_origin() -> str:
    value = os.environ.get("GREEN_ATLAS_BRIDGE_ORIGIN", "").rstrip("/")
    try:
        parsed = urlsplit(value)
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        parsed = urlsplit("")
        valid_port = False
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.netloc
        or not valid_port
        or any(char.isspace() or ord(char) < 32 for char in value)
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and local))
    ):
        raise HTTPException(
            503,
            detail={
                "code": "BRIDGE_DISABLED",
                "message": "Подключение AutoCAD ещё не настроено",
            },
        )
    return value


def store() -> TransferStore:
    public_origin()
    # A configured origin is not enough: require a real trusted-proxy identity.
    if len(os.environ.get("GREEN_ATLAS_BRIDGE_PROXY_KEY", "")) < 32:
        raise HTTPException(
            503,
            detail={
                "code": "BRIDGE_DISABLED",
                "message": "Подтверждение пользователя не настроено",
            },
        )
    return TransferStore(get_cad_intake().config.storage / "transfers.sqlite3")


def browser_user(request: Request) -> str:
    origin = public_origin()
    expected = os.environ.get("GREEN_ATLAS_BRIDGE_PROXY_KEY", "")
    supplied = request.headers.get("X-Green-Atlas-Proxy-Key", "")
    owner = request.headers.get("X-Green-Atlas-User", "")
    if (
        len(expected) < 32
        or not hmac.compare_digest(expected.encode(), supplied.encode())
        or not owner
        or len(owner) > 256
        or any(ord(char) < 32 for char in owner)
    ):
        raise HTTPException(
            401,
            detail={
                "code": "BROWSER_LOGIN_REQUIRED",
                "message": "Войдите в сервис через браузер",
            },
        )
    if request.method != "GET" and request.headers.get("origin") != origin:
        raise HTTPException(
            403,
            detail={
                "code": "ORIGIN_REQUIRED",
                "message": "Подтвердите передачу на странице сервиса",
            },
        )
    return owner


def token(
    value: str = Header(
        alias="X-Green-Atlas-Transfer-Token", min_length=43, max_length=128
    ),
) -> str:
    return value


def call(action):
    try:
        return action()
    except TransferError as error:
        raise HTTPException(
            error.status,
            detail={"code": error.code, "message": str(error)},
            headers={"Retry-After": "5"} if error.status == 429 else None,
        ) from error


def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


@router.post("/device/transfers", response_model=TransferReceipt, status_code=201)
def create(
    body: TransferCreate, response: Response, storage: TransferStore = Depends(store)
):
    private_response(response)
    status, code = call(lambda: storage.create(body))
    return TransferReceipt(
        **status.model_dump(),
        confirmation_code=code,
        verification_url=f"{public_origin()}/connect/autocad/{status.id}",
    )


@router.get("/device/transfers/{transfer_id}", response_model=TransferStatus)
def device_status(
    transfer_id: UUID,
    response: Response,
    secret: str = Depends(token),
    storage: TransferStore = Depends(store),
):
    private_response(response)
    return call(lambda: storage.device_status(str(transfer_id), secret))


@router.post("/device/transfers/{transfer_id}/cancel", response_model=TransferStatus)
def cancel(
    transfer_id: UUID,
    response: Response,
    secret: str = Depends(token),
    storage: TransferStore = Depends(store),
):
    private_response(response)
    return call(lambda: storage.cancel(str(transfer_id), secret))


@router.get("/approvals/{transfer_id}", response_model=TransferReview)
def review(
    transfer_id: UUID,
    response: Response,
    owner: str = Depends(browser_user),
    storage: TransferStore = Depends(store),
):
    private_response(response)
    status, manifest, version = call(lambda: storage.review(str(transfer_id), owner))
    return TransferReview(
        **status.model_dump(), manifest=manifest, plugin_version=version
    )


@router.post("/approvals/{transfer_id}", response_model=TransferStatus)
def decide(
    transfer_id: UUID,
    body: TransferDecision,
    response: Response,
    owner: str = Depends(browser_user),
    storage: TransferStore = Depends(store),
):
    private_response(response)
    return call(lambda: storage.decide(str(transfer_id), owner, body))


def upload_store(storage: TransferStore = Depends(store)) -> UploadStore:
    return call(lambda: UploadStore(storage))


@router.post("/device/transfers/{transfer_id}/upload", response_model=UploadProgress)
def begin_upload(
    transfer_id: UUID,
    response: Response,
    secret: str = Depends(token),
    storage: UploadStore = Depends(upload_store),
):
    private_response(response)
    return call(lambda: storage.begin(str(transfer_id), secret))


@router.get("/device/transfers/{transfer_id}/upload", response_model=UploadProgress)
def upload_progress(
    transfer_id: UUID,
    response: Response,
    secret: str = Depends(token),
    storage: UploadStore = Depends(upload_store),
):
    private_response(response)
    return call(lambda: storage.progress(str(transfer_id), secret))


@router.put(
    "/device/transfers/{transfer_id}/files/{file_index}",
    response_model=UploadProgress,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
)
async def upload_chunk(
    transfer_id: UUID,
    file_index: int,
    request: Request,
    response: Response,
    offset: int = Query(ge=0),
    digest: str = Header(alias="X-Chunk-SHA256", pattern=r"^[0-9a-f]{64}$"),
    secret: str = Depends(token),
    storage: UploadStore = Depends(upload_store),
):
    private_response(response)
    progress = await run_in_threadpool(
        call, lambda: storage.progress(str(transfer_id), secret)
    )
    if not 0 <= file_index < len(progress.files):
        raise HTTPException(
            404,
            detail={
                "code": "FILE_NOT_LISTED",
                "message": "Файл отсутствует в комплекте",
            },
        )
    if (
        request.headers.get("content-type", "").split(";")[0]
        != "application/octet-stream"
    ):
        raise HTTPException(
            415, detail={"code": "CHUNK_TYPE", "message": "Ожидается часть файла"}
        )
    data = bytearray()
    try:
        async with asyncio.timeout(60):
            async for part in request.stream():
                if len(data) + len(part) > CHUNK_BYTES:
                    raise HTTPException(
                        413,
                        detail={
                            "code": "CHUNK_SIZE",
                            "message": "Часть файла слишком велика",
                        },
                    )
                data.extend(part)
    except TimeoutError as error:
        raise HTTPException(
            408,
            detail={
                "code": "UPLOAD_TIMEOUT",
                "message": "Отправьте часть файла повторно",
            },
        ) from error
    return await run_in_threadpool(
        call,
        lambda: storage.append(
            str(transfer_id), secret, file_index, offset, bytes(data), digest
        ),
    )


@router.post(
    "/device/transfers/{transfer_id}/upload/finish", response_model=UploadProgress
)
def finish_upload(
    transfer_id: UUID,
    response: Response,
    secret: str = Depends(token),
    storage: UploadStore = Depends(upload_store),
):
    private_response(response)
    return call(lambda: storage.finish(str(transfer_id), secret))


def publication(
    storage: UploadStore = Depends(upload_store),
    runtime: Runtime = Depends(get_runtime),
):
    return TransferPublication(storage, runtime)


@router.post(
    "/device/transfers/{transfer_id}/publication",
    response_model=PublicationReceipt,
    status_code=202,
)
def publish_transfer(
    transfer_id: UUID,
    body: PublicationRequest,
    response: Response,
    tasks: BackgroundTasks,
    secret: str = Depends(token),
    service: TransferPublication = Depends(publication),
):
    private_response(response)
    receipt, claim = call(lambda: service.start(str(transfer_id), secret, body))
    if claim:
        tasks.add_task(service.run, str(transfer_id), secret, claim)
    elif receipt.status == "needs_review":
        tasks.add_task(service.resume_review, str(transfer_id), secret)
    return receipt


@router.get(
    "/device/transfers/{transfer_id}/publication", response_model=PublicationReceipt
)
def publication_status(
    transfer_id: UUID,
    response: Response,
    secret: str = Depends(token),
    service: TransferPublication = Depends(publication),
):
    private_response(response)
    return call(lambda: service.status(str(transfer_id), secret))
