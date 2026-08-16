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

## وبهوک + TLS (روی دامنه‌ی واقعی)

بات به‌صورت پیش‌فرض **long-poll** می‌کنه و هیچ پورت ورودی لازم نداره. برای سوییچ به وبهوک، توی
`.env` این‌ها رو ست کن:

| کلید | مقدار |
|---|---|
| `WEBHOOK_URL` | `https://hero.spayerx.ir` |
| `WEBHOOK_SECRET` | با `openssl rand -hex 32` بساز — هم مسیر URL وبهوکه، هم هدرِ تأیید |
| `PANEL_DOMAIN` | `hero.spayerx.ir` (برای پروکسی همراه) |
| `DJANGO_ALLOWED_HOSTS` | `hero.spayerx.ir` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://hero.spayerx.ir` |
| `DJANGO_BEHIND_PROXY` | `true` |
| `DJANGO_SECURE_COOKIES` | `true` |
| `ADMIN_PANEL_URL` | `https://hero.spayerx.ir/panel/` |

**اگه سرور پروکسی خودش رو نداره**، پروکسی همراه (Caddy) رو با پروفایل `proxy` بالا بیار — خودش
گواهی TLS رو از Let's Encrypt می‌گیره:

```bash
docker compose --profile proxy up -d --build
```

این Caddy فقط پورت‌های ۸۰ و ۴۴۳ هاست رو می‌گیره و بقیه‌ی سرویس‌ها روی شبکه‌ی داخلی compose می‌مونن.
یه دامنه، دو مقصد: مسیر `/<WEBHOOK_SECRET>` می‌ره به بات، بقیه‌ی مسیرها به پنل.

**اگه سرور از قبل nginx/caddy/traefik داره** (تا با بقیه‌ی چیزهایی که روی VPS رانن تداخل نکنه)،
پروفایل `proxy` رو بالا نیار و پروکسیِ موجود رو این‌طوری تنظیم کن:

- `hero.spayerx.ir/<WEBHOOK_SECRET>` → `web` نه، بلکه کانتینر `bot` روی پورت `8443`
- بقیه‌ی `hero.spayerx.ir` → کانتینر `web` روی پورت `8000`

برای اینکه پروکسیِ موجود به کانتینرها برسه، این دو پورت رو روی loopback هاست منتشر کن (توی یه
`docker-compose.override.yml`):

```yaml
services:
  web:
    ports: ["127.0.0.1:8000:8000"]
  bot:
    ports: ["127.0.0.1:8443:8443"]
```

نمونه‌ی nginx برای این حالت:

```nginx
server {
    server_name hero.spayerx.ir;
    location /<WEBHOOK_SECRET> { proxy_pass http://127.0.0.1:8443; }
    location /                 { proxy_pass http://127.0.0.1:8000; }
    # ... بلوک TLS/certbot خودت
}
```

بات موقع بالا اومدن خودش وبهوک رو روی تلگرام ست می‌کنه؛ نیازی به `setWebhook` دستی نیست. برای
چک کردن: `curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"`.

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
