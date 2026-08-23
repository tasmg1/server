FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# تثبيت المكتبات الأساسية فقط
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ ملفات المشروع
COPY . .

# تشغيل Gunicorn بـ 4 Workers و Uvicorn Worker لتحمّل أكثر من 1000 اتصال متزامن بكفاءة
CMD exec gunicorn -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8080} --access-logfile - --error-logfile - main:app
