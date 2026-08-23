import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.gzip import GZipMiddleware

app = FastAPI(
    title="Security Form Fast Engine",
    docs_url=None,  # تعطيل التوثيق في الإنتاج لتقليل استهلاك الذاكرة
    redoc_url=None
)

# ضغط الملفات والاستجابات لتسريع التحميل وتوفير استهلاك الباندويث لآلاف المستخدمين
app.add_middleware(GZipMiddleware, minimum_size=1000)

templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def serve_form(request: Request):
    return templates.TemplateResponse(
        "index.html", 
        {
            "request": request,
            "is_pdf": False,
            "font_size": 16
        }
    )

# نقطة فحص الحالة السريعة لـ Health Checks على Railway
@app.get("/healthz")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="warning")
