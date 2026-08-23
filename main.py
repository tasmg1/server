import os
import urllib.parse
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Dict, Optional
from playwright.async_api import async_playwright

app = FastAPI(title="Security Form Enterprise Engine")
templates = Jinja2Templates(directory="templates")

class FormDataPayload(BaseModel):
    data: Dict[str, str]
    font_size: Optional[int] = 16

playwright_instance = None
browser_instance = None

@app.on_event("startup")
async def startup_event():
    global playwright_instance, browser_instance
    playwright_instance = await async_playwright().start()
    browser_instance = await playwright_instance.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--font-render-hinting=none"
        ]
    )

@app.on_event("shutdown")
async def shutdown_event():
    global browser_instance, playwright_instance
    if browser_instance:
        await browser_instance.close()
    if playwright_instance:
        await playwright_instance.stop()

@app.get("/", response_class=HTMLResponse)
async def serve_form(request: Request):
    template = templates.get_template("index.html")
    html_content = template.render(
        request=request,
        form_data={},
        font_size=16,
        is_pdf=False
    )
    return HTMLResponse(content=html_content)

@app.post("/api/export-pdf")
async def export_pdf_endpoint(request: Request, payload: FormDataPayload):
    template = templates.get_template("index.html")
    rendered_html = template.render(
        request=request,
        form_data=payload.data,
        font_size=payload.font_size,
        is_pdf=True
    )

    # إنشاء صفحة بحجم A4 الدقيق بدقة 2x
    page = await browser_instance.new_page(
        viewport={"width": 794, "height": 1123},
        device_scale_factor=2
    )
    
    await page.set_content(rendered_html, wait_until="networkidle")
    await page.emulate_media(media="print")

    # إنتاج ملف PDF قياسي مطابق للطباعة
    pdf_bytes = await page.pdf(
        format="A4",
        print_background=True,
        prefer_css_page_size=True,
        margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"}
    )
    await page.close()

    filename = "نموذج_رقم_1.pdf"
    encoded_filename = urllib.parse.quote(filename)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
        }
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
