import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Security Form Fast Engine")

# تحديد مجلد القوالب الذي يحتوي على ملف index.html
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def serve_form(request: Request):
    # إرسال صفحة الواجهة للمستخدم بسرعة فائقة
    return templates.TemplateResponse(
        "index.html", 
        {
            "request": request,
            "is_pdf": False,
            "font_size": 16
        }
    )

if __name__ == "__main__":
    import uvicorn
    # ربط البورت التلقائي الخاص بـ Railway
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
