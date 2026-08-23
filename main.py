import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.gzip import GZipMiddleware

app = FastAPI(
    title="Security Form Fast Engine",
    docs_url=None,
    redoc_url=None
)

# ضغط الملفات لتسريع التحميل لآلاف المستخدمين
app.add_middleware(GZipMiddleware, minimum_size=1000)

# تحديد المسارات المحتملة لملف index.html لتجنب أي خطأ في المسار
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "templates" / "index.html"
ROOT_HTML_PATH = BASE_DIR / "index.html"

@app.get("/", response_class=HTMLResponse)
async def serve_form():
    # التحقق من وجود الملف في مجلد templates أو المسار الرئيسي
    if TEMPLATE_PATH.exists():
        return FileResponse(
            path=TEMPLATE_PATH, 
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"}
        )
    elif ROOT_HTML_PATH.exists():
        return FileResponse(
            path=ROOT_HTML_PATH, 
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"}
        )
    return HTMLResponse("<h2>خطأ: لم يتم العثور على ملف index.html</h2>", status_code=404)

@app.get("/healthz")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
