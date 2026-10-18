<div dir="rtl">

# ‏Heardloop עם n8n

> English version: [examples/n8n/README.md](../../examples/n8n/README.md)

## בשביל מה זה

‏Heardloop יכול לפנות לכתובת שלכם בכל פעם שקורה משהו: פוסט ממתין לאישור, אושר או הוחזר, פורסם או נכשל; חיבור
לינקדאין עומד לפוג או צריך חיבור מחדש; גיבוי נכשל; הסיכום השבועי מוכן. ‏[n8n](https://n8n.io) (כלי אוטומציה
שמותקן אצלכם, בדומה ל-Zapier) מקבל את הפנייה ומבצע את מה שבניתם אחריה. ‏Heardloop עובד מצוין גם בלי זה; זה
למי שרוצה שהאירועים יגיעו לכלים אחרים.

רעיונות:

| מתי | מה עושים |
| --- | --- |
| ‏`post.failed`, ‏`account.reconnect_needed`, ‏`backup.failed` | הודעה מיידית אליכם בטלגרם, בסלאק או במייל |
| ‏`post.submitted` | הודעה למאשר עם קישור לפוסט |
| ‏`post.published` | שורה חדשה ב-Google Sheets או ב-Notion (תאריך, קישור, קמפיין) |
| ‏`post.approved` | הזזת כרטיס בלוח משימות |
| ‏`digest.weekly` | העברת הנתונים השבועיים לערוץ של הצוות |

רשימת האירועים המלאה ומבנה ההודעה: [docs/WEBHOOKS.md](../WEBHOOKS.md) (באנגלית).

## מה ה-workflow לדוגמה עושה

‏`examples/n8n/heardloop-webhook.json` הוא נקודת התחלה, בלי שירותים חיצוניים:

1. ‏**Heardloop webhook** מקבל את הפנייה.
2. ‏**Build signed string**, ‏**HMAC SHA256** ו-**Check signature** מוודאים שהפנייה באמת הגיעה מה-Heardloop שלכם
   (אחרת כל מי שמנחש את הכתובת יכול לשלוח אירועים מזויפים). גם פניות ישנות (יותר מ-5 דקות) נדחות.
3. ‏**Signature valid?** עונה `200` לפנייה אמיתית ו-`401` לכל השאר (Heardloop מנסה שוב פניות שלא קיבלו 2xx אחרי
   1, 5, 15 ו-60 דקות).
4. ‏**Needs attention?** שולח את `post.failed`, ‏`account.reconnect_needed`, ‏`account.expiring`, ‏`backup.failed`
   ו-`backup.stale` אל **Alert**, וכל השאר אל **Log event**. שניהם רק מנסחים הודעה; אחריהם מחברים פעולה משלכם
   (טלגרם, מייל, Sheets...).

השדות שיוצאים: `event`, ‏`post_id`, ‏`post_status`, ‏`post_url`, ‏`error`, ‏`occurred_at`, ורק כשבהגדרות
Heardloop מסומן **לצרף את טקסט הפוסט**: ‏`post_text`, ‏`comment` (הערת המאשר), ‏`actor_name` ו-`account_name`.
הם ריקים באירועים שלא קשורים לפוסט (למשל `webhook.test`) או כשאין מה לצרף (ל-`post.published` אין הערת מאשר).

## הגדרה

1. ‏**n8n ← Workflows ← Import from File** ובוחרים את `heardloop-webhook.json`.
2. ‏**Heardloop ← הגדרות ← Webhook** (בעלים, עם אימות דו-שלבי): מכניסים כתובת זמנית ושומרים. ‏Heardloop מציג את
   ה-**secret פעם אחת**. מעתיקים אותו.
3. ב-n8n פותחים את **HMAC SHA256** ומחליפים את `PASTE_WEBHOOK_SECRET_HERE` ב-secret. מרגע זה לא מייצאים ולא
   משתפים את ה-workflow, כי ה-secret בתוכו. (אם ב-n8n שלכם יש Credentials או משתני סביבה לסודות, עדיף להשתמש בהם.)
4. פותחים את **Heardloop webhook**, מעתיקים את ה-**Test URL** ולוחצים **Listen for test event**.
5. חוזרים ל-Heardloop, מדביקים את הכתובת ושומרים. הכתובת חייבת להיות נגישה **מתוך הקונטיינרים של Heardloop**:
   - ‏n8n במחשב אחר ברשת: ‏`http://<הכתובת שלו ברשת>:5678/webhook-test/heardloop`.
   - ‏n8n ב-Docker על אותו מחשב: ‏`http://host.docker.internal:5678/webhook-test/heardloop`.  <!-- privacy-allow -->
   - ‏`localhost` או `127.0.0.1` לא עובדים: בתוך הקונטיינר הם מצביעים על Heardloop עצמו, ו-Heardloop חוסם אותם
     בכוונה.
6. ‏**שליחת בדיקה.** ב-n8n מופיעה הרצה אחת שמסתיימת ב-**Log event** עם `event = webhook.test`; ברשימת המשלוחים
   ב-Heardloop מופיע **נשלח 200**.
7. בדיקה שלילית (לא חובה): משנים תו אחד ב-secret ב-n8n ושולחים שוב: ‏n8n עונה **401**, וב-Heardloop המשלוח
   מסומן "ממתין" ויישלח שוב. מחזירים את ה-secret הנכון.
8. כשהכל עובד: מעתיקים את ה-**Production URL**, מפעילים את ה-workflow ב-n8n (Active) ומעדכנים את הכתובת
   ב-Heardloop.

אם ה-secret נחשף אי פעם (צילום מסך, צ'אט): ‏**החלפת secret** ב-Heardloop ועדכון ב-n8n.

## איך החתימה עובדת

‏Heardloop שולח `X-Heardloop-Timestamp` ו-`X-Heardloop-Signature = "sha256=" + HMAC-SHA256(secret,
"<timestamp>.<body>")`. גוף ההודעה הוא JSON עם מפתחות ממוינים, בלי רווחים, ותווים שאינם ASCII מקודדים
כ-`\uXXXX`. ‏n8n מעביר ל-workflow את ה-JSON אחרי פענוח, ולכן **Build signed string** בונה מחדש בדיוק את אותם
בתים לפני החישוב. זה עובד לכל הודעה של Heardloop (מחרוזות, מספרים שלמים, ערכי אמת, null).

</div>
