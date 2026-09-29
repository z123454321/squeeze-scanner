# Use the Squeeze Scanner on your iPhone

The agent is now a **mobile web app**. You open it in Safari (or Chrome) on your iPhone.  
For the best experience, add it to your Home Screen so it feels like a real app.

There are two easy ways to run it.

---

## Option A — Free cloud (recommended, works anywhere)

This gives you a permanent link you can open from your phone even when you’re away from home.

### 1. Put the code on GitHub
1. Create a free GitHub account if you don’t have one.
2. Create a new public repository (e.g. `squeeze-scanner`).
3. Upload these two files from this project:
   - `squeeze_app.py`
   - (optional) `requirements.txt` — create one with:

```
streamlit
requests
beautifulsoup4
```

### 2. Deploy on Streamlit Community Cloud (free)
1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. Click **New app**.
3. Select your repository, branch `main`, and set the main file path to:
   ```
   squeeze_app.py
   ```
4. Click **Deploy**.
5. In 1–2 minutes you’ll get a public URL like:
   ```
   https://yourname-squeeze-scanner.streamlit.app
   ```

### 3. Open on iPhone & add to Home Screen
1. Open the Streamlit URL in **Safari**.
2. Tap the **Share** button (square with arrow).
3. Scroll and tap **Add to Home Screen**.
4. Name it “Squeeze Scanner” (or whatever you like) → Add.
5. The icon now appears on your home screen and opens full-screen like a native app.

---

## Option B — Run on your computer (local Wi-Fi)

Useful for testing or if you prefer not to use the cloud.

### On a Mac / Windows / Linux machine:
```bash
cd /path/to/this/folder
pip install streamlit requests beautifulsoup4
streamlit run squeeze_app.py
```

Streamlit will print a local address, usually:
```
http://localhost:8501
```

### From your iPhone (same Wi-Fi):
1. Find your computer’s local IP (e.g. `192.168.1.42`).
2. On the iPhone open Safari and go to:
   ```
   http://192.168.1.42:8501
   ```
3. Add to Home Screen the same way as above.

> Note: Your computer must stay on and the Streamlit process must keep running.

---

## How to use the app on the phone

1. Tap **Scan Market** (big blue button).
2. Wait 15–40 seconds while it pulls Finviz + optional FINRA data.
3. Scroll the ranked list.  
   - **High** (red) and **Elevated** (orange) badges = strongest positioning.
   - Tap **Score breakdown** under any stock for the exact points.
4. Adjust filters (min short %, volume, price, market cap) in the **Filters** section, then scan again.

Data is cached for ~15 minutes so repeated opens are fast.

---

## Files in this project

| File | Purpose |
|------|---------|
| `squeeze_app.py` | The mobile web app (Streamlit) |
| `high_short_interest_agent.py` | Original command-line agent |
| `README_short_interest_agent.md` | CLI documentation |
| `IPHONE_SETUP.md` | This guide |

---

## Important reminders

- This is a **research heuristic**, not a trading signal or financial advice.
- Short squeezes are rare and can reverse violently.
- The score measures *crowding / potential*, not the probability that a squeeze will actually occur tomorrow.
- Always do your own research and manage risk.

Enjoy the scanner on your phone.
