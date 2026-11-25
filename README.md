# GTA 6 PC release checker

A tiny page that tells you whether GTA 6 has a PC release date. The checker itself uses plain rules, not an AI.
(The code was written with the help of an AI.)

- `check.py` reads gaming news feeds and the Steam store once a day, looks for sentences about the PC version, and pulls out dates (only dates within the next 2 years that sit next to a PC word, not a console word).
- Official sources (Rockstar, Take-Two, Steam) count more than news sites, and rumor wording is labeled as a rumor.
- `.github/workflows/check.yml` runs it every day on GitHub for free and saves `result.json`.
- `index.html` shows the answer in a square. Add it to your phone's home screen to use it like an app.
- Notifications: devices register from the page's menu (Notification settings). `push.py` and `.github/workflows/push.yml` send the notifications; `sw.js` shows them.
