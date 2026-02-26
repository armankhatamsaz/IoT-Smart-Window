import json
import os
from flask import Flask, jsonify, request

app = Flask(__name__)
CATALOG_FILE = 'catalog.json'

# این تابع فایل جیسون رو میخونه تا اطلاعات قبلی از بین نره
def load_catalog():
    if os.path.exists(CATALOG_FILE):
        with open(CATALOG_FILE, 'r') as f:
            return json.load(f)
    return {"devices": [], "services": []}

# این تابع اطلاعات جدید رو توی فایل ذخیره میکنه
def save_catalog(data):
    with open(CATALOG_FILE, 'w') as f:
        json.dump(data, f, indent=4)

@app.route('/', methods=['GET'])
def home():
    return jsonify({"message": "Welcome to the Smart Window Device & Service Registry!"})

# گرفتن لیست دستگاه‌ها
@app.route('/devices', methods=['GET'])
def get_devices():
    catalog = load_catalog()
    return jsonify(catalog["devices"])

# ثبت یک دستگاه جدید (سنسور یا اکچویتور)
@app.route('/devices', methods=['POST'])
def register_device():
    new_device = request.get_json()
    catalog = load_catalog()
    catalog["devices"].append(new_device)
    save_catalog(catalog)
    return jsonify({"message": "Device registered successfully!", "device": new_device}), 201

if __name__ == '__main__':
    # اگه فایل دیتابیس وجود نداشت، همون اول یکی میسازه
    if not os.path.exists(CATALOG_FILE):
        save_catalog({"devices": [], "services": []})
    
    app.run(port=8080, debug=True)