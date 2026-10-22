<div dir="rtl">

# מדריך התקנה

המדריך לוקח אתכם ממחשב ריק עד הפוסט המתוזמן הראשון בלינקדאין. זה לוקח בערך 30 דקות.
הפקודות מוצגות ל-Windows PowerShell. ב-Linux וב-macOS הפקודות זהות, עם סקריפטים בסיומת `.sh`.

> English version: [docs/INSTALL.md](../INSTALL.md)

**תוכן העניינים**

1. [מה צריך](#1-מה-צריך)
2. [הורדת Heardloop](#2-הורדת-heardloop)
3. [יצירת קובץ הסודות](#3-יצירת-קובץ-הסודות)
4. [יצירת אפליקציית מפתחים בלינקדאין](#4-יצירת-אפליקציית-מפתחים-בלינקדאין)
5. [הכנסת המפתחות של לינקדאין ל-`.env`](#5-הכנסת-המפתחות-של-לינקדאין-ל-env)
6. [הפעלת Heardloop](#6-הפעלת-heardloop)
7. [יצירת חשבון הבעלים](#7-יצירת-חשבון-הבעלים)
8. [כניסה ראשונה ואימות דו-שלבי](#8-כניסה-ראשונה-ואימות-דו-שלבי)
9. [חיבור פרופיל הלינקדאין](#9-חיבור-פרופיל-הלינקדאין)
10. [הפוסט הראשון](#10-הפוסט-הראשון)
11. [גיבויים ועדכונים](#11-גיבויים-ועדכונים)
12. [פתרון תקלות](#12-פתרון-תקלות)

---

## 1. מה צריך

| פריט | הערות |
| --- | --- |
| מחשב שדולק כשפוסטים מתוזמנים | Windows 10/11, macOS או Linux. הפוסטים יוצאים מהמחשב שלכם; אם הוא במצב שינה, הם יוצאים כשהוא מתעורר. |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | מתקינים, מפעילים פעם אחת ומחכים ל-"Engine running". ב-Windows הוא יבקש להפעיל WSL 2: מאשרים. |
| [Git](https://git-scm.com/downloads) | להורדה ולעדכון של Heardloop. |
| חשבון לינקדאין | הפרופיל שממנו תפרסמו. |
| דף לינקדאין (LinkedIn Page) שאתם מנהלים | לינקדאין מחייבת לקשר כל אפליקציית מפתחים לדף. כל דף קטן מתאים; הפוסטים לא מתפרסמים בו. |

בדיקה ש-Docker מותקן, ב-PowerShell:

```powershell
docker --version
docker compose version
```

שתי הפקודות צריכות להדפיס מספר גרסה.

## 2. הורדת Heardloop

בוחרים תיקייה שבה Heardloop יישב (הוא שומר בה את הקבצים שלו), ואז:

```powershell
git clone https://github.com/HirezRa/heardloop.git
cd heardloop
git checkout v<latest>   # הגרסה החדשה ביותר מדף ה-Releases, למשל v0.9.6
```

עבודה עם תג גרסה (ולא עם הענף `main`) אומרת שאתם מריצים גרסה שנבדקה, וגם שהודעת העדכון באפליקציה עובדת.

## 3. יצירת קובץ הסודות

```powershell
powershell -ExecutionPolicy Bypass -File scripts/new-env.ps1
```

הסקריפט יוצר את `.env` עם סיסמת מסד נתונים אקראית ומפתח הצפנה אקראי (`HEARDLOOP_TOKEN_KEY`).
ב-Linux/macOS: ‏`cp .env.example .env` ולפי ההערות שבקובץ.

> **גבו את `.env` עכשיו** (במנהל סיסמאות או בדיסק-און-קי מוצפן). בלי `HEARDLOOP_TOKEN_KEY` גיבוי משוחזר לא
> יוכל לקרוא את חיבורי הלינקדאין ואת סודות האימות הדו-שלבי. לעולם לא מעלים את `.env` ל-Git, לא שולחים אותו לאף
> אחד, ולא מגדירים `HEARDLOOP_TOKEN_KEY` או `POSTGRES_PASSWORD` כמשתנה בחלון הפקודות: משתנה כזה גובר על `.env`,
> ואז Heardloop מסרב לעלות.

## 4. יצירת אפליקציית מפתחים בלינקדאין

Heardloop מדבר עם לינקדאין דרך אפליקציית מפתחים חינמית משלכם. רק אתם משתמשים בה, והיא לא יוצאת מהמחשב שלכם.
בצילומים למטה המזהים האמיתיים מוסתרים; אצלכם יופיעו הערכים שלכם. המסכים של לינקדאין באנגלית, ולכן שמות
הכפתורים כתובים כאן באנגלית.

### 4.1 פתיחת פורטל המפתחים

נכנסים ל-<https://www.linkedin.com/developers/apps> עם חשבון הלינקדאין שממנו תפרסמו, ולוחצים **Create app**.

![פורטל המפתחים, My apps](../assets/install/linkedin-01-my-apps.png)

### 4.2 מילוי פרטי האפליקציה

| שדה | מה למלא |
| --- | --- |
| **App name** | כל שם שתזהו, למשל `Heardloop (personal)`. השם מופיע במסך האישור של לינקדאין. |
| **LinkedIn Page** | בחרו דף לינקדאין שאתם מנהלים. אם אתם מפתחים פרטיים, ייתכן שלינקדאין תדרוש לבחור את דף ברירת המחדל שלה למפתחים פרטיים - פעלו לפי ההנחיה שמתחת לשדה. אי אפשר לשנות את הבחירה אחרי שהאפליקציה נשמרת. |
| **Privacy policy URL** | לא חובה. לשימוש אישי משאירים ריק. |
| **App logo** | חובה. כל תמונה ריבועית (PNG/JPG, לפחות 100 על 100 פיקסלים). אפשר את `docs/assets/logo.png` מהריפו. |
| **Legal agreement** | קוראים ומסמנים. |

לוחצים **Create app**.

![טופס יצירת אפליקציה](../assets/install/linkedin-02-create-app.png)

### 4.3 אימות האפליקציה מול הדף (אם מתבקש)

בלשונית **Settings** ייתכן שיופיע "Verify" ליד הדף. לוחצים, אחר כך **Generate URL**, פותחים את הכתובת כשאתם
מחוברים כמנהלי הדף, ומאשרים. אחרי זה האפליקציה מסומנת "Verified". לא כל חשבון מתבקש לעשות את זה; אם הכפתור לא
מופיע, מדלגים.

![לשונית Settings, אימות דף](../assets/install/linkedin-03-verify.png)

### 4.4 הוספת שני המוצרים

פותחים את לשונית **Products** ומבקשים גישה לשניים:

1. **Sign In with LinkedIn using OpenID Connect**: מאפשר ל-Heardloop לדעת בשם איזה פרופיל הוא מפרסם.
2. **Share on LinkedIn**: מאפשר ל-Heardloop לפרסם פוסטים (ההרשאה `w_member_social`).

לכל אחד: **Request access**, מסמנים את התנאים, ושוב **Request access**. שניהם בשירות עצמי ובדרך כלל מאושרים מיד;
אחרי האישור הם מופיעים תחת **Added products**.

![לשונית Products עם שני המוצרים](../assets/install/linkedin-04-products.png)

לא צריך שום דבר נוסף: לא את "Community Management API" ולא מוצרי שיווק.

> תנאי השימוש ב-API של לינקדאין מגבילים פרסום אוטומטי (סעיף 3.1). ‏Heardloop מפרסם פוסט בשעה שאדם קבע,
> דרך ה-API הרשמי ובהרשאה לפרסם בשם המשתמש המחובר בלבד; כל פוסט נכתב ומתוזמן על ידי אדם. ההחלטה אם פרסום
> מתוזמן מתאים לשימוש שלכם בלינקדאין היא שלכם: קראו את
> [תנאי השימוש](https://www.linkedin.com/legal/l/api-terms-of-use), ואם יש ספק פנו ללינקדאין. מי שמעדיף לפרסם
> כל פוסט ידנית יכול להשתמש ב**פרסום עכשיו** במקום בתזמון.

### 4.5 הוספת כתובת החזרה (Redirect URL)

פותחים את לשונית **Auth**. תחת **OAuth 2.0 settings**, ליד **Authorized redirect URLs for your app**, לוחצים על
העיפרון, אחר כך **Add redirect URL**, ומקלידים בדיוק:

```text
http://localhost:8380/api/v1/linkedin/callback
```

לוחצים **Update**. הכתובת חייבת להיות זהה תו בתו: `http`, ‏`localhost`, פורט `8380`, בלי לוכסן בסוף. אם שיניתם
פורט או שאתם מריצים בכתובת אחרת, רושמים אותה כאן ומגדירים את אותו ערך ב-`LINKEDIN_REDIRECT_URI` שב-`.env`.

![לשונית Auth, כתובת החזרה](../assets/install/linkedin-05-redirect.png)

### 4.6 בדיקת ההרשאות (Scopes)

עדיין ב-**Auth**: באזור **OAuth 2.0 scopes** צריכים להופיע `openid`, ‏`profile` ו-`w_member_social` (ייתכן שגם
`email`; ‏Heardloop לא מבקש אותו). אם `w_member_social` חסר, שלב 4.4 לא הושלם.

![לשונית Auth, הרשאות](../assets/install/linkedin-06-scopes.png)

### 4.7 העתקת Client ID ו-Client Secret

בראש לשונית **Auth**, תחת **Application credentials**:

- **Client ID**: מעתיקים.
- **Primary Client Secret**: לוחצים על אייקון העין ומעתיקים.

![לשונית Auth, פרטי גישה (מטושטש)](../assets/install/linkedin-07-credentials.png)

> ה-Client Secret הוא סיסמה של האפליקציה שלכם. מדביקים אותו רק ב-`.env` (בשלב הבא). לא בצ'אט, לא במייל, לא
> בצילום מסך ולא בפנייה לתמיכה. אם הוא דלף: **Auth -> Generate a new Client Secret**, מעדכנים את `.env`
> ומפעילים מחדש את Heardloop.

## 5. הכנסת המפתחות של לינקדאין ל-`.env`

פותחים את `.env` בעורך טקסט (גם Notepad מתאים) וממלאים:

```dotenv
LINKEDIN_CLIENT_ID=<ה-Client ID שלכם>
LINKEDIN_CLIENT_SECRET=<ה-Client Secret שלכם>
LINKEDIN_REDIRECT_URI=http://localhost:8380/api/v1/linkedin/callback
```

שומרים. את שאר השורות לא משנים.

## 6. הפעלת Heardloop

```powershell
docker compose up -d --build
```

הבנייה הראשונה לוקחת כמה דקות. בדיקה שהכל רץ:

```powershell
docker compose ps
```

‏`db`, ‏`api`, ‏`worker`, ‏`web` ו-`backup` צריכים להיות `running` (ו-`api` ו-`db` גם `healthy`).

## 7. יצירת חשבון הבעלים

פעם אחת, לאדם הראשון:

```powershell
docker compose exec api python -m heardloop.devtools.create_owner --email <המייל-שלכם> --name "<השם שלכם>" --workspace "<שם ה-Workspace>"
```

אופציונלי: `--time-zone Asia/Jerusalem` (שם IANA). אם לא מציינים, Heardloop משתמשת באזור הזמן המקומי של
המחשב כשהוא שם IANA מוכר, אחרת `UTC`. Workspace חדש ברירת המחדל שלו היא `UTC` כשלא מעבירים דגל.

הפקודה מבקשת סיסמה (לפחות 12 תווים) ולא מציגה אותה. אין הרשמה פתוחה: הבעלים מוסיף אנשים נוספים אחר כך
ב-**הגדרות ← משתמשים**.

## 8. כניסה ראשונה ואימות דו-שלבי

1. פותחים את <http://localhost:8380> ונכנסים.
2. **הגדרות ← כניסה ואבטחה ← הפעלת אימות דו-שלבי.** סורקים את קוד ה-QR באפליקציית אימות (Microsoft
   Authenticator, ‏Google Authenticator, ‏1Password ועוד), מקלידים את הקוד בן 6 הספרות ומאשרים.
3. **שומרים את קודי השחזור** במקום בטוח. כל קוד עובד פעם אחת, למקרה שהטלפון אבד.

בעלים לא יכולים לנהל משתמשים, חשבונות לינקדאין, webhook או יומן פעולות לפני שהאימות הדו-שלבי פעיל.

![הגדרות, כניסה ואבטחה](../assets/screenshots/settings-security.png)

לא חובה, באותו מסך: **הגדרות ← תצוגה** לשפה (עברית/אנגלית), מצב כהה, גודל טקסט ופורמט תאריך.

## 9. חיבור פרופיל הלינקדאין

1. **חשבונות ← חיבור לינקדאין.**
2. לינקדאין מציגה מסך אישור עם שם האפליקציה שלכם. בודקים שזה הפרופיל הנכון ולוחצים **Allow**.
3. חוזרים ל-Heardloop; החשבון מסומן **פעיל** ומוצג תאריך פקיעת החיבור.

![חשבון לינקדאין מחובר](../assets/screenshots/accounts.png)

חיבור ללינקדאין תקף כ-60 יום. ‏Heardloop מזכיר לבעלים 7 ימים לפני; חיבור מחדש הוא אותן שתי לחיצות, וכל
הפוסטים נשמרים.

![מסך חשבונות עם פרופיל מחובר](../assets/screenshots/accounts.png)

## 10. הפוסט הראשון

1. **פוסטים ← פוסט חדש.** בוחרים חשבון, כותבים, ואפשר להוסיף תמונה, סרטון או כרטיס קישור.
2. התצוגה המקדימה מראה איך לינקדאין יציג את הפוסט, כולל איפה "...see more" חותך.
3. **שמירה**, ואז **תזמון** לתאריך ושעה (באזור הזמן של ה-Workspace) או **פרסום עכשיו**.
4. תוך כ-15 שניות מהמועד הפוסט באוויר, ומגיעה התראה עם קישור אליו.

![עורך הפוסט עם תצוגה מקדימה](../assets/screenshots/posts-editor.png)

[מדריך המשתמש](../GUIDE.md) (באנגלית) מסביר אישורים, קמפיינים, לוח שנה, תבניות, מדדים, דוחות וייבוא.

## 11. גיבויים ועדכונים

- **גיבויים** רצים כל לילה לתיקייה `backups` (ההגדרה `BACKUP_DIR`). **הגדרות ← Workspace ← גיבויים** מציג את
  האחרון. לגיבוי מיידי: `scripts/backup-now.ps1`. שחזור מוסבר ב-[מדריך המשתמש, סעיף 10](../GUIDE.md#10-backups-and-restore).
- **עדכונים**: כשמתפרסמת גרסה חדשה, הבעלים רואים הודעה באפליקציה עם מה שהשתנה. להתקנה:

  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts/update.ps1
  ```

  הסקריפט מגבה קודם, מוריד את הגרסה החדשה, בונה ומפעיל מחדש, ובודק ש-Heardloop חזר תקין. הוא לא נוגע ב-`.env`.
  עם `-VerifyTag` (או `HEARDLOOP_VERIFY_TAG=1`; ב-`update.sh` זה `--verify-tag`) הסקריפט מסרב לתג גרסה בלי חתימה
  תקפה ממפתח ש-git שלכם סומך עליו. אם הבדיקה נכשלת, שום דבר לא משתנה.

## 12. פתרון תקלות

| מה רואים | הסיבה והפתרון |
| --- | --- |
| לינקדאין כותבת **"The redirect_uri does not match the registered value"** | הכתובת בשלב 4.5 שונה מ-`LINKEDIN_REDIRECT_URI` ב-`.env`. משווים ביניהן (בלי לוכסן בסוף) ומריצים `docker compose up -d`. |
| ‏Heardloop כותב **"לינקדאין לא נתנה הרשאת פרסום" (`missing_scope`)** | ‏**Share on LinkedIn** לא נוסף (שלב 4.4). מוסיפים ומתחברים שוב. |
| לינקדאין מציגה **"unauthorized_scope_error"** | אחד משני המוצרים חסר או עדיין ממתין (שלב 4.4). |
| הכפתור **חיבור לינקדאין** אפור | ‏`LINKEDIN_CLIENT_ID` / ‏`LINKEDIN_CLIENT_SECRET` ריקים ב-`.env`, או שלא הופעל מחדש אחרי העריכה. |
| פוסט נכשל עם **"דורש חיבור מחדש"** | החיבור פג או בוטל. **חשבונות ← חיבור** שוב, ואז **תזמון מחדש של פוסטים שנכשלו** באותו מסך. |
| ‏Heardloop לא עולה, ובלוג **"HEARDLOOP_TOKEN_KEY does not match"** | ‏`.env` הוחלף או שמשתנה בחלון הפקודות גובר עליו. משחזרים את ה-`.env` המגובה; ב-PowerShell מריצים `Remove-Item Env:HEARDLOOP_TOKEN_KEY` ומפעילים שוב. |
| ‏**http://localhost:8380** לא נפתח | ‏Docker Desktop לא רץ, או שתוכנה אחרת תופסת את פורט 8380. ‏`docker compose ps` מראה מה רץ; משנים פורט ב-`docker-compose.override.yml`. |
| פוסט מתוזמן יצא באיחור | המחשב היה במצב שינה או כבוי. הפוסט יוצא כשהמחשב חוזר. |
| ב«אודות» מופיע **לא ניתן לבדוק עדכונים** עם סיבת TLS / תעודה | פרוקסי או אנטי־וירוס בודקים HTTPS. מייצאים את תעודת השורש של הארגון לקובץ `.pem`, מחברים אותו לקריאה בלבד לקונטיינרים של api ו-worker, ומגדירים `EXTRA_CA_CERT_FILE` לנתיב בתוך הקונטיינר (ראו למטה). **אין** לבטל אימות תעודות. |

**תעודת שורש ארגונית לבדיקת העדכונים (בדיקת TLS)**

1. מייצאים את תעודת השורש של הארגון כ-PEM (ב-Windows לעיתים «Base-64 encoded X.509 (.CER)»; מוודאים שזה טקסט PEM שמתחיל ב-`BEGIN CERTIFICATE`).
2. שמים את הקובץ ליד `docker-compose.yml`, למשל `certs/org-root.pem` (לא ב-git).
3. ב-`docker-compose.override.yml` (לא ב-git) מחברים את הקובץ ומגדירים את המשתנה:

```yaml
services:
  api:
    volumes:
      - ./certs/org-root.pem:/certs/org-root.pem:ro
    environment:
      EXTRA_CA_CERT_FILE: /certs/org-root.pem
  worker:
    volumes:
      - ./certs/org-root.pem:/certs/org-root.pem:ro
    environment:
      EXTRA_CA_CERT_FILE: /certs/org-root.pem
```

4. מריצים `docker compose up -d` ואז **הגדרות ← אודות ← בדיקה עכשיו**. רק בדיקת העדכונים מול GitHub משתמשת בתעודה הזו; לינקדאין, SMTP ו-webhooks נשארים עם מאגר האמון הרגיל.

עדיין תקועים? פותחים Issue ב-GitHub עם טקסט השגיאה מהאפליקציה או מ-`docker compose logs api worker`
(לפני שמפרסמים, מוודאים שאין בטקסט כתובות מייל, טוקנים או כתובות IP).

## 13. אופציונלי: עוזר כתיבה (AI)

בעורך הפוסטים אפשר להפעיל **עוזר כתיבה** (שיפור, קיצור, פתיחה חזקה יותר, תיקון שגיאות, הצעת האשטגים, תרגום).
הוא כבוי עד שמחברים את Heardloop לשרת שתומך ב-API התואם ל-OpenAI, `/v1/chat/completions` (למשל Ollama, LM Studio או vLLM).
כשמישהו משתמש בעוזר, טקסט הפוסט (או החלק המסומן) נשלח לשרת הזה. Heardloop לא שומר כלום. עדיף מודל שמריצים לבד,
כך שהטקסט לא יוצא מהרשת שלכם.

דוגמה: Ollama כשירות נוסף בקובץ `docker-compose.override.yml` (לא נשמר ב-git):

```yaml
services:
  ollama:
    image: ollama/ollama:<version>   # pin a release tag
    restart: unless-stopped
    volumes:
      - ollama:/root/.ollama
volumes:
  ollama:
```

```powershell
docker compose up -d ollama
docker compose exec ollama ollama pull llama3.1:8b
```

ואז ב-`.env`:

```dotenv
AI_BASE_URL=http://ollama:11434/v1
AI_MODEL=llama3.1:8b
```

ולבסוף `docker compose up -d api`. מודל בגודל הזה צריך כמה GB של זיכרון פנוי, ועונה תוך שניות עד דקה במחשב רגיל.
`AI_TIMEOUT_SECONDS` (ברירת מחדל 60) הוא הזמן המקסימלי לבקשה.

- Ollama במחשב אחר ברשת: `AI_BASE_URL=http://<that machine>:11434/v1`.
- אפשר גם שירות מקוון (`AI_API_KEY` נשלח כ-Bearer token), אבל אז טקסט הפוסטים יוצא מהרשת שלכם.
- כל משתמש יכול לשלוח 10 בקשות בדקה. צופים (viewer) לא רואים את העוזר.

**הזמנים הטובים** ליד שדה התזמון לא צריכים AI: הם מחושבים מהמדדים של הפוסטים שלכם, ומופיעים אחרי שלכמה פוסטים יש נתונים.

</div>
