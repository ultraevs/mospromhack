from __future__ import annotations

import re
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from backend.app.services.sources import IMAGE_EXTENSIONS
from backend.app.state import AppState, get_current_user, get_state, require_admin

router = APIRouter(prefix="/api", tags=["uploads"])

UPLOAD_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov"}
MAX_UPLOAD = 500 * 1024 * 1024  # на всё за раз
MAX_ZIP_FILES = 5000
CHUNK = 1024 * 1024


def safe_name(name: str) -> str:
    # только последний кусок пути, без странных символов
    base = PurePosixPath((name or "").replace("\\", "/")).name
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = re.sub(r"[^\w\-]+", "_", stem, flags=re.UNICODE).strip("._")[:80]
    if not stem:
        raise HTTPException(422, f"плохое имя файла: {name}")
    return f"{stem}.{ext.lower()}" if ext else stem


def _ext(name: str) -> str:
    return Path(name or "").suffix.lower()


def _check_raw(name: str) -> None:
    n = (name or "").replace("\\", "/")
    if ".." in n.split("/") or n.startswith("/") or ":" in n:
        raise HTTPException(422, f"путь в имени файла не разрешён: {name}")


def _free(path: Path) -> Path:
    if not path.exists():
        return path
    return path.with_name(f"{path.stem}_{int(time.time())}{path.suffix}")


def _save(f: UploadFile, dst: Path, budget: list[int]) -> None:
    with open(dst, "wb") as out:
        while chunk := f.file.read(CHUNK):
            budget[0] -= len(chunk)
            if budget[0] < 0:
                out.close()
                dst.unlink(missing_ok=True)
                raise HTTPException(413, "слишком большой файл, максимум 500 МБ")
            out.write(chunk)


def _unzip(src: Path, folder: Path, budget: list[int]) -> int:
    n = 0
    try:
        zf = zipfile.ZipFile(src)
    except zipfile.BadZipFile:
        raise HTTPException(422, "zip битый") from None
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and _ext(i.filename) in IMAGE_EXTENSIONS]
        if len(infos) > MAX_ZIP_FILES:
            raise HTTPException(422, "в zip слишком много файлов")
        for info in infos:
            if PurePosixPath(info.filename.replace("\\", "/")).name.startswith("."):
                continue  # мусор от макоса
            budget[0] -= info.file_size
            if budget[0] < 0:
                raise HTTPException(413, "zip распаковывается больше чем в 500 МБ")
            dst = _free(folder / safe_name(info.filename))  # пути из zip выкидываем
            with zf.open(info) as i, open(dst, "wb") as o:
                shutil.copyfileobj(i, o, CHUNK)
            n += 1
    return n


@router.post("/uploads")
def upload(files: list[UploadFile] = File(...), _user=Depends(require_admin), state: AppState = Depends(get_state)) -> dict:
    data = state.settings.data_dir
    for f in files:
        _check_raw(f.filename)
    exts = [_ext(f.filename) for f in files]
    budget = [MAX_UPLOAD]

    if any(e in UPLOAD_VIDEO_EXTENSIONS for e in exts):
        if len(files) != 1:
            raise HTTPException(422, "видео грузится по одному файлу")
        videos = data / "videos"
        videos.mkdir(parents=True, exist_ok=True)
        dst = _free(videos / safe_name(files[0].filename))
        _save(files[0], dst, budget)
        return {"source_type": "file", "source": dst.name}

    bad = [f.filename for f, e in zip(files, exts) if e not in IMAGE_EXTENSIONS and e != ".zip"]
    if bad:
        raise HTTPException(422, f"такие файлы не принимаем: {', '.join(bad)}")

    first = files[0].filename
    name = safe_name(Path(first).stem if len(files) == 1 else f"set_{time.strftime('%Y%m%d_%H%M%S')}")
    images = data / "images"
    folder = _free(images / name)
    folder.mkdir(parents=True)
    count = 0
    try:
        for f, e in zip(files, exts):
            if e == ".zip":
                tmp = folder / ".upload.zip"
                _save(f, tmp, budget)
                count += _unzip(tmp, folder, budget)
                tmp.unlink(missing_ok=True)
            else:
                _save(f, _free(folder / safe_name(f.filename)), budget)
                count += 1
        if count == 0:
            raise HTTPException(422, "картинок не нашлось")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return {"source_type": "images", "source": folder.name}


@router.get("/images")
async def list_images(_user=Depends(get_current_user), state: AppState = Depends(get_state)) -> list[dict]:
    images = state.settings.data_dir / "images"
    if not images.exists():
        return []
    out = []
    for p in sorted(images.iterdir()):
        if p.is_dir():
            n = sum(1 for f in p.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS)
            out.append({"name": p.name, "count": n})
    return out
