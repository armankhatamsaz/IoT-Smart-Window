# استفاده از نسخه سبک پایتون
FROM python:3.9-slim

# تنظیم پوشه کاری داخل کانتینر
WORKDIR /app

# کپی کردن فایل نیازمندی‌ها و نصب آن‌ها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کپی کردن تمام فایل‌های پایتون به داخل کانتینر
COPY . .