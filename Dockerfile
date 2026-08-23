FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# تثبيت حزم الخطوط العربية المعتمدة
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-core \
    fonts-noto-extra \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# تثبيت مكتبات البايثون
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ ملفات المشروع
COPY . .

# تشغيل الخادم مع قراءة المنفذ التلقائي الخاص بـ Railway
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
