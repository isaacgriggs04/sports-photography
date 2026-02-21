# Instagram OAuth (Business Login) Setup

This app supports linking Instagram accounts via OAuth instead of typing usernames manually. Photographers can click "Link with Instagram" to connect their professional account.

## Requirements

- **Instagram professional account** (Business or Creator) – personal accounts are not supported by the API
- **Meta Developer account** and app with Instagram product

## Setup Steps

### 1. Create a Meta App

1. Go to [developers.facebook.com](https://developers.facebook.com)
2. Create an app (or use existing)
3. Add product: **Instagram** → **Instagram API setup with Instagram login**

### 2. Configure Business Login

In App Dashboard → Instagram → API setup with Instagram login → Set up business login:

1. **Instagram App ID** and **Instagram App Secret** – note these
2. **OAuth redirect URIs** – add your callback URL(s):
   - Local: `http://localhost:8080/api/instagram/callback`
   - Production: `https://yourdomain.com/api/instagram/callback`

### 3. Environment Variables

Add to your `.env`:

```
INSTAGRAM_APP_ID=your_instagram_app_id
INSTAGRAM_APP_SECRET=your_instagram_app_secret
INSTAGRAM_REDIRECT_URI=http://localhost:8080/api/instagram/callback
FRONTEND_ORIGIN=http://localhost:5173
```

For production, use your actual domain:

```
INSTAGRAM_REDIRECT_URI=https://yoursite.com/api/instagram/callback
FRONTEND_ORIGIN=https://yoursite.com
```

### 4. Access Level

- **Standard Access** – Only your own account or accounts you manage (no app review)
- **Advanced Access** – Any photographer can link (requires App Review + Business Verification)

For development, Standard Access is enough. Add your Instagram account as a test user in the App Dashboard.

## Flow

1. User clicks "Link with Instagram" in their profile
2. Redirected to Instagram to authorize
3. Instagram redirects to `/api/instagram/callback` with a code
4. Backend exchanges code for token, fetches username, saves to profile
5. User redirected back to app with success message
