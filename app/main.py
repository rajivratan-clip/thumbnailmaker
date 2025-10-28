from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uuid
import os
from .thumbnailer import generate_thumbnail_and_upload
from typing import Optional
import tempfile
import shutil

app = FastAPI(title="Clip Thumbnailer Service")
app.mount("/static", StaticFiles(directory="."), name="static")

class GenerateRequest(BaseModel):
    video_url: str  # accept local path or http(s)
    time: Optional[float] = None         # seconds (optional)
    title: Optional[str] = None
    width: Optional[int] = 1280
    height: Optional[int] = 720
    key_prefix: Optional[str] = "thumbnails/"

class GenerateResponse(BaseModel):
    url: str
    s3_key: str
    width: int
    height: int

@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    # create unique filename/key
    name = str(uuid.uuid4()) + ".jpg"
    s3_key = os.path.join((req.key_prefix or "thumbnails/").strip("/"), name)
    try:
        url = generate_thumbnail_and_upload(
            video_url=str(req.video_url),
            out_key=s3_key,
            width=req.width,
            height=req.height,
            time=req.time,
            title=req.title
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return GenerateResponse(url=url, s3_key=s3_key, width=req.width, height=req.height)


@app.post("/upload-generate", response_model=GenerateResponse)
async def upload_generate(
    file: UploadFile = File(...),
    time: Optional[float] = Form(None),
    title: Optional[str] = Form(None),
    width: Optional[int] = Form(1280),
    height: Optional[int] = Form(720),
    key_prefix: Optional[str] = Form("thumbnails/")
):
    name = str(uuid.uuid4()) + ".jpg"
    s3_key = os.path.join((key_prefix or "thumbnails/").strip("/"), name)
    try:
        with tempfile.TemporaryDirectory() as tmpd:
            src_path = os.path.join(tmpd, file.filename or "upload.mp4")
            with open(src_path, "wb") as out_f:
                shutil.copyfileobj(file.file, out_f)
            url = generate_thumbnail_and_upload(
                video_url=src_path,
                out_key=s3_key,
                width=width,
                height=height,
                time=time,
                title=title
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return GenerateResponse(url=url, s3_key=s3_key, width=width, height=height)