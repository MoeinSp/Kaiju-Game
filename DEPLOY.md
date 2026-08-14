# استقرار روی VPS

بات و پنل از **یک ایمیج مشترک** ساخته می‌شن و به **یک دیتابیس Postgres** وصلن. یعنی هر تغییری که
توی پنل بدی (رنگ دکمه‌ها، ایموجی‌ها، لودآوت) بلافاصله توی بات زنده‌ست — بدون ری‌استارت.

## پیش‌نیاز

Docker و Docker Compose روی سرور. همین.

## راه‌اندازی

```bash
git clone <repo> kaiju && cd kaiju
cp .env.example .env
```

توی `.env` این‌ها رو حتماً پر کن:

| کلید | توضیح |
|---|---|
| `BOT_TOKEN` | توکن بات از @BotFather |
| `OWNER_TELEGRAM_ID` | شناسه‌ی عددی خودت (دسترسی دستورات سازنده) |
| `DJANGO_SECRET_KEY` | با `python -c "import secrets; print(secrets.token_urlsafe(50))"` بساز |
| `POSTGRES_PASSWORD` | یه رمز قوی |
| `POSTGRES_DB` | مثلاً `kaiju` — پر کردنش یعنی «برو روی Postgres» |
| `DJANGO_ALLOWED_HOSTS` | دامنه‌ی واقعی پنل |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://panel.example.com` (حتماً با `https://`) |

بعد:

```bash
docker compose up -d --build
```

و یه بار حساب ورود پنل بساز:

```bash
docker compose exec web python manage.py createsuperuser
```

پنل روی `http://127.0.0.1:8000/panel/` بالا میاد. **عمداً فقط روی loopback باز می‌شه** — جلوش
nginx یا caddy بذار تا TLS بگیره، و بعد `DJANGO_BEHIND_PROXY=true` و `DJANGO_SECURE_COOKIES=true`
رو ست کن.

نمونه‌ی caddy:

```
panel.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

## دستورهای روزمره

```bash
docker compose logs -f bot          # لاگ بات
docker compose logs -f web          # لاگ پنل
docker compose restart bot          # ری‌استارت فقط بات
docker compose exec web python manage.py migrate
docker compose down && docker compose up -d --build   # دیپلوی نسخه‌ی جدید
```

## پشتیبان‌گیری

از بخش «💾 پشتیبان‌گیری» پنل. فایل‌ها توی ولوم `backups` می‌شینن، پس با
`docker compose down` از بین نمی‌رن. برای بردنشون بیرون از سرور، از خود پنل دکمه‌ی دانلود رو بزن،
یا:

```bash
docker compose cp web:/app/backups ./backups-local
```

فرمت پشتیبان، JSON فشرده‌ی سریالایزر جنگوئه نه دامپ باینری — برای همین یه پشتیبانی که روی
SQLite لوکال گرفتی، مستقیم روی Postgres سرور بازیابی می‌شه.

## مهاجرت از SQLite لوکال به سرور

۱. لوکال: پنل رو بالا بیار (`python manage.py runserver`)، یه پشتیبان بگیر و دانلودش کن.
۲. سرور: `docker compose up -d --build` و `createsuperuser`.
۳. توی پنل سرور، فایل رو آپلود کن و بعد «بازیابی» بزن.

⚠️ بازیابی همه‌ی داده‌های فعلی رو پاک می‌کنه و حساب‌های ورود پنل رو هم از فایل برمی‌گردونه.

## اجرای لوکال بدون Docker

```bash
pip install -r requirements.txt
cp .env.example .env      # POSTGRES_DB رو خالی بذار تا SQLite بمونه
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver     # پنل: http://127.0.0.1:8000/panel/
python -m bot.main             # بات، توی یه ترمینال دیگه
```
