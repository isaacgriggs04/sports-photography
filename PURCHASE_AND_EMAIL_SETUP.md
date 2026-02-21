# Purchase confirmation and email setup

After a customer pays with Stripe, the app:

1. **Shows a “Purchase confirmed” modal** with download links (when they return from Stripe).
2. **Stores the purchase** in `purchases.json` (used to allow downloads).
3. **Sends an email** to the account email with a receipt and the purchased photos attached.

## Required: Stripe webhook (so purchases are recorded and email is sent)

1. In Stripe Dashboard → Developers → Webhooks, add an endpoint:
   - **URL:** `https://your-backend-host/api/stripe-webhook`
   - **Events:** `checkout.session.completed`
2. Copy the **Signing secret** (starts with `whsec_`).
3. In your backend `.env` add:
   ```env
   STRIPE_WEBHOOK_SECRET=whsec_xxxxx
   ```

Without this, payments still succeed but purchases are not recorded and no email is sent.

## Optional: SMTP (to send receipt + photos by email)

If these are set, the backend sends the receipt and photo attachments to the customer’s email:

```env
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your-smtp-user
SMTP_PASSWORD=your-smtp-password
FROM_EMAIL=noreply@yourdomain.com
```

If SMTP is not configured, the app logs “Purchase email skipped” and does not send email; the rest of the flow (confirmation UI, recording, downloads) still works.

## Downloading purchased photos

- From the **Purchase confirmed** modal: user clicks **Download** (requires being signed in with the same account that bought the photos).
- The same photos can be downloaded later by signing in and using any link that calls the download API for that photo (e.g. a “My purchases” page if you add one). Purchases are keyed by Clerk user id.
