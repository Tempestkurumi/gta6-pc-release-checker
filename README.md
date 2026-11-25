# GTA 6 PC release checker

A tiny page that tells you whether GTA 6 has a PC release date.

- `check.py` reads gaming news feeds and the Steam store once a day, looks for sentences about the PC version, and pulls out dates (only dates within the next 2 years that sit next to a PC word, not a console word).
- Official sources (Rockstar, Take-Two, Steam) count more than news sites, and rumor wording is labeled as a rumor.
- `.github/workflows/check.yml` runs it every day on GitHub for free and saves `result.json`.
- `index.html` shows the answer in a square. Add it to your phone's home screen to use it like an app.
# Page
- https://tempestkurumi.github.io/gta6-pc-release-checker/
# Installation on phone
- Open the page in either Chrome or Safari.
- On iPhone use Safari → Share → Add to Home Screen. On Android, use the Chrome menu → Add to Home Screen.

# AI disclosure
AI was used to write this code in its entirety.
As this is not meant to be downloaded and only to host a webpage, it should be fine from a security standpoint.
