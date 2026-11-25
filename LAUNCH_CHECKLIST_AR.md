# 🚀 دليل الإطلاق السحابي الشامل لمنصة ريت باث (RatePath) — تشغيل مجاني 24/7

هذا الدليل العملي يشرح بالترتيب الدقيق الأوامر والخطوات اللازمة لربط روابط الأرباح، ورفع الكود إلى **GitHub**، ونشر الواجهة الأمامية على **Cloudflare Pages**، وتشغيل المحرك الخلفي المستقل على مدار الساعة مجاناً عبر **Hugging Face Spaces** مع تنشيط النبض الدائم عبر **cron-job.org**.

---

## أولاً: إعداد روابط تحقيق الدخل والقنوات (ملف واحد فقط)

افتح الملف [`frontend/monetization.config.js`](./frontend/monetization.config.js) وضع روابطك الحقيقية في الحقول المخصصة دون الحاجة لتعديل أي ملف برمجي آخر:

1. **روابط الإحالة الذكية (`affiliates`)**:
   - `wise` و `remitly`: تظهر تلقائياً داخل المحول عند اختيار أي عملة نقدية (`USD`, `EUR`, `GBP`, `SAR`, `AED`, `EGP`, `TRY`).
   - `bybit` و `binance`: تظهر تلقائياً عند اختيار الذهب (`XAU_OZ`, `24K`, `21K`, `18K`) أو العملات الرقمية (`BTC`, `ETH`, `SOL`).
2. **إعلانات A-Ads (`advertising.aAdsUnitId`)**:
   - ضع رقم الوحدة الإعلانية فقط (مثال: `"2398451"`) ليتم عرض الإعلان الحي تلقائياً.
3. **اشتراك VIP بالعملات الرقمية (`vipPayments.nowPaymentsVipUrl`)**:
   - ضع رابط الدفع الخاص بك من لوحة تحكم **NOWPayments**.
4. **قنوات وبوت تيليجرام (`telegram`) — 4 قنوات مفصولة حسب اللغة**:
   - `publicChannelEn` و `vipChannelEn`: تظهر تلقائياً لزوار الواجهة الإنجليزية (`EN`).
   - `publicChannelAr` و `vipChannelAr`: تظهر تلقائياً لزوار الواجهة العربية (`AR`).
   - `botUrl` و `miniAppUrl` (رابط موقعك على Cloudflare Pages).

---

## ثانياً: تهيئة قاعدة البيانات (Supabase) والذاكرة السريعة (Upstash Redis)

1. **قاعدة بيانات Supabase (`pgvector`)**:
   - أنشئ مشروعاً مجانياً على [Supabase](https://supabase.com).
   - افتح **SQL Editor**، انسخ كامل محتوى [`backend/schema.sql`](./backend/schema.sql) وشغّله لإنشاء جدول `market_memory` وفهارس `pgvector` ودالة المطابقة التاريخية.
   - احتفظ بـ `SUPABASE_URL` ومفتاح `service_role` لاستخدامه كـ `SUPABASE_KEY` داخل المتغيرات السرية السحابية.
2. **ذاكرة منع التكرار Upstash Redis**:
   - أنشئ قاعدة Redis مجانية على [Upstash](https://upstash.com) وانسخ `UPSTASH_REDIS_REST_URL` و `UPSTASH_REDIS_REST_TOKEN` (تضمن عدم نشر نفس رابط الخبر مرتين على تيليجرام).
3. **بوت تيليجرام (`@BotFather`)**:
   - أنشئ البوت عبر `@BotFather` واحصل على `TELEGRAM_BOT_TOKEN`.
   - أضف البوت كمشرف (**Admin**) مع صلاحية النشر في قناتك العامة (`TELEGRAM_PUBLIC_CHANNEL`) وقناة VIP (`TELEGRAM_VIP_CHANNEL`).
   - اربط تطبيق الويب المصغر (Mini App) عبر الأمر `/newapp` في `@BotFather` وضع رابط موقعك على Cloudflare Pages.

---

## ثالثاً: رفع المشروع بأمان إلى GitHub (من داخل PowerShell)

> **ملاحظة سريعة لمستخدمي Windows:** إذا لم يكن برنامج `Git` مثبّتاً على جهازك بعد، ثبّته بأمر واحد رسمي في PowerShell ثم أعد فتح النافذة:
> ```powershell
> winget install --id Git.Git -e --source winget
> ```

> **تنبيه أمني صارم:** ملف `.env` محجوب تلقائياً عبر `.gitignore` ولن يتم رفعه أبداً. تحقّق دائماً بالأمر الموضح أدناه قبل الرفع.

شغّل الأوامر التالية بالترتيب داخل مجلد المشروع في **PowerShell**:

```powershell
$env:PYTHONUTF8="1"

# 1. تهيئة المستودع المحلي والتحقق الصارم من أن ملف المفاتيح السرية .env مستبعد ومحمّي
git init
git check-ignore -v .env
git status

# 2. إضافة جميع ملفات المنصة المعتمدة وإنشاء أول إصدار (Commit)
git add .
git status
git commit -m "chore: initial production release of RatePath (Gate 1-4)"

# 3. ربط المستودع بحسابك على GitHub ورفع الكود (استبدل YOUR_USERNAME باسم حسابك)
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/ratepath.git
git push -u origin main
```

---

## رابعاً (الخيار المجاني 100% الموصى به): التشغيل السيرفرليس عبر GitHub Actions (بديل مجاني بالكامل بدون PRO)

يعمل ملف [`.github/workflows/ratepath_worker.yml`](./.github/workflows/ratepath_worker.yml) مع [`backend/run_cycle.py`](./backend/run_cycle.py) مجاناً على **GitHub Actions** كل 5 دقائق لتنفيذ:
- جلب الأسعار الحية والأخبار والتحليل الثنائي عبر مجمع الـ 6 مفاتيح والنشر على قنوات تيليجرام الأربع.
- تشغيل حلقة التقييم الذاتي (`evaluator.py`) تلقائياً مرة واحدة كل 60 دقيقة عبر مفتاح `ratepath:last_eval_ts` في **Upstash Redis**.
- رفع ملف `live_feed.json` **فقط** في Commit يتيم نظيف واحد إلى فرع مستقل اسمه `live-data` (مما يمنع تضخم المستودع ويمنع إعادة بناء Cloudflare Pages كل 5 دقائق).

### خطوات التفعيل على GitHub Actions:
1. افتح مستودعك على GitHub -> **Settings -> Secrets and variables -> Actions -> New repository secret** وأضف جميع المفاتيح السرية الـ 17:
   - `GEMINI_API_KEY_1`، `GEMINI_API_KEY_2`، `GEMINI_API_KEY_3`
   - `GROQ_API_KEY_1`، `GROQ_API_KEY_2`، `GROQ_API_KEY_3`، `OPENROUTER_API_KEY`
   - `SUPABASE_URL`، `SUPABASE_KEY` (مفتاح `sb_secret_...`)
   - `UPSTASH_REDIS_REST_URL`، `UPSTASH_REDIS_REST_TOKEN`
   - `TELEGRAM_BOT_TOKEN`، `ADMIN_CHAT_ID`
   - `TELEGRAM_PUBLIC_CHANNEL_EN`، `TELEGRAM_VIP_CHANNEL_EN`، `TELEGRAM_PUBLIC_CHANNEL_AR`، `TELEGRAM_VIP_CHANNEL_AR`
   - (اختياري) `MINI_APP_URL` برابط موقعك على Cloudflare Pages.
2. افتح تبويب **Actions** -> اختر **RatePath 24/7 Serverless Market & Macro Worker** واضغط **Run workflow** للتجربة الفورية الأولى.
3. بعد نجاح التشغيل الأول، افتح [`frontend/monetization.config.js`](./frontend/monetization.config.js) وضع رابط البث الحي المجاني في `LIVE_FEED_URL`:
   ```javascript
   export const LIVE_FEED_URL = "https://raw.githubusercontent.com/YOUR_USERNAME/ratepath/live-data/live_feed.json";
   ```
   ثم ارفع التحديث إلى `main` ليقرأ موقع Cloudflare Pages الأسعار الحية كل 5 دقائق مباشرةً ومجاناً بالكامل.

---

## بديل إضافي (اختياري): نشر المحرك الخلفي المستقل عبر Docker / Hugging Face Spaces

يعمل ملف [`Dockerfile`](./Dockerfile) مع [`backend/cloud_worker.py`](./backend/cloud_worker.py) على المنفذ `7860` لتنفيذ دورتين آليتين:
- **كل 5 دقائق:** جلب أسعار العملات والذهب والعملات الرقمية وفروق التمويل والأخبار العالمية، وتحديث `/live_feed.json` ونشر الجديد على تيليجرام.
- **كل 60 دقيقة:** تشغيل حلقة التقييم الذاتي (`evaluator.py`) لمراجعة دقة توقعات الـ 24 ساعة.

### خطوات النشر على Hugging Face Spaces:

1. ادخل إلى [Hugging Face Spaces](https://huggingface.co/spaces) واضغط **Create new Space**:
   - **Space Name:** `ratepath-worker`
   - **Space SDK:** اختر **Docker** (قالب **Blank**).
   - **Visibility:** **Public** (ليتمكن موقع Cloudflare Pages وخدمة النبض من قراءة `/live_feed.json` و `/health` عبر CORS).
2. من تبويب **Settings -> Variables and secrets** داخل الـ Space، أضف المفاتيح السرية (**New secret**) التالية بقيمك الحقيقية:
   - `GEMINI_API_KEY` (واختيارياً `GROQ_API_KEY` و `OPENROUTER_API_KEY`)
   - `SUPABASE_URL` و `SUPABASE_KEY`
   - `UPSTASH_REDIS_REST_URL` و `UPSTASH_REDIS_REST_TOKEN`
   - `TELEGRAM_BOT_TOKEN` و `TELEGRAM_PUBLIC_CHANNEL_EN` و `TELEGRAM_VIP_CHANNEL_EN` و `TELEGRAM_PUBLIC_CHANNEL_AR` و `TELEGRAM_VIP_CHANNEL_AR` و `ADMIN_CHAT_ID`
   - `MINI_APP_URL` (رابط واجهتك على Cloudflare Pages، مثال: `https://ratepath.pages.dev`)
3. ارفع الكود مباشرة إلى Hugging Face Space من جهازك:

```powershell
$env:PYTHONUTF8="1"

# إضافة Hugging Face Space كوجهة نشر ثانية (استبدل HF_USERNAME باسم حسابك على Hugging Face)
git remote add hf https://huggingface.co/spaces/HF_USERNAME/ratepath-worker
git push hf main
```

4. بعد اكتمال البناء وظهور حالة **Running**، سيكون لديك رابطان مباشران يعملان فوراً:
   - فحص الصحة: `https://HF_USERNAME-ratepath-worker.hf.space/health`
   - البث الحي للأسعار والأخبار (مع دعم CORS): `https://HF_USERNAME-ratepath-worker.hf.space/live_feed.json`

5. **تفعيل النبض المجاني الدائم 24/7 عبر `cron-job.org`**:
   - أنشئ حساباً مجانياً على [cron-job.org](https://cron-job.org).
   - أنشئ مهمة جديدة (**Create Cronjob**) تزور الرابط:
     `https://HF_USERNAME-ratepath-worker.hf.space/health`
   - اضبط الجدول الزمني على **كل 10 دقائق** (`*/10 * * * *`) لضمان عدم دخول الحاوية في وضع السكون مطلقاً.

---

## خامساً: نشر الواجهة الأمامية على Cloudflare Pages وربط البث الحي

1. افتح [`frontend/monetization.config.js`](./frontend/monetization.config.js) وضع رابط الـ Space في حقل `cloudFeedUrl`:
   ```javascript
   cloudFeedUrl: "https://HF_USERNAME-ratepath-worker.hf.space/live_feed.json",
   ```
   ثم ارفع التحديث إلى GitHub:
   ```powershell
   $env:PYTHONUTF8="1"
   git add frontend/monetization.config.js
   git commit -m "feat: connect live Hugging Face cloud feed endpoint"
   git push origin main
   ```

2. ادخل إلى لوحة تحكم **Cloudflare Dashboard -> Workers & Pages -> Create -> Pages -> Connect to Git**:
   - اختر مستودع `ratepath` من حسابك في GitHub.
   - اضبط إعدادات البناء (**Build settings**) كالتالي بالضبط:
     - **Production branch:** `main`
     - **Framework preset:** `None`
     - **Build command:** *(اتركه فارغاً تماماً)*
     - **Build output directory:** `frontend`
   - اضغط **Save and Deploy**.

3. سيقوم Cloudflare Pages تلقائياً بتفعيل:
   - ترويسات الحماية والتخزين المؤقت السريع من [`frontend/_headers`](./frontend/_headers).
   - التوجيه الثنائي النظيف (`/ar` و `/en`) من [`frontend/_redirects`](./frontend/_redirects).
   - ملفات أرشفة محركات البحث العالمية [`frontend/sitemap.xml`](./frontend/sitemap.xml) و [`frontend/robots.txt`](./frontend/robots.txt).
   - تطبيق الويب التقدمي (PWA) الذي يعمل حتى بدون اتصال بالإنترنت، وتكامل **Telegram Mini App** الكامل.
