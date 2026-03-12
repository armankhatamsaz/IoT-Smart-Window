FROM python:3.9-slim

# این خط باعث میشه لاگ‌های پایتون درجا تو ترمینال چاپ بشن و گیر نکنن
ENV PYTHONUNBUFFERED=1 

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# برای رجیستری: CMD ["python", "main.py"]
# برای بقیه: CMD ["python", "اسم_فایل_اصلی.py"]