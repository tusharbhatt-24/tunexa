# 🎵 Tunexa — Premium Media Downloader & Playlist Sync 🚀

---

## ✨ Overview
**Tunexa** is a gorgeous, state-of-the-art web application that combines the power of an **instant media downloader** (supporting up to 4K video & 320kbps MP3s) and a **seamless playlist transfer utility** between Spotify and YouTube Music.

Engineered with a visual-first aesthetic, fluid micro-animations, glassmorphism UI cards, and a robust Python/FastAPI backend, Tunexa runs fully locally with **zero external dependencies** or mandatory developer API credentials!

---

## ⚡ Main Features

### 1. 📥 Tunexa Media Downloader
*   **Spotify & YouTube Support**: Simply paste any YouTube video, YouTube playlist, Spotify track, or Spotify playlist URL.
*   **Credentials-Free Spotify Extraction 🔓**: Tunexa embeds an advanced public metadata scraper. Download Spotify songs and full playlists **without** registering or configuring Spotify developer client keys!
*   **Granular Playlist Checklist 📋**: When pasting playlist links, Tunexa fetches and lists all tracks with checkboxes. Deselect the ones you don't want, then click **one button** to download them all in a single compressed ZIP file.
*   **320 kbps High-Fidelity Audio 🎧**: Extract and encode audio into premium MP3 format using custom-compiled FFmpeg parameters.
*   **4K Ultra-HD Video 📺**: Download videos in true 4K resolution (where available) by dynamically merging audio and high-definition video streams locally.

### 2. 🔄 Playlist Transfer Engine
*   **Spotify ➡️ YouTube Music**: Sign in securely via OAuth in one click, load your private or public playlists, and transfer them instantly.
*   **Interactive Visual Mapping**: Match tracks accurately using ISRCs and smart title-artist search heuristics, with visual progression overlays.

### 3. 🔒 Safe & Self-Cleaning
*   **100% Secure**: All OAuth tokens are held in-memory and never stored on the disk.
*   **Auto-Cleanup Utility**: A background thread running on the FastAPI backend automatically purges downloaded tracks and temporary directory assets after **1 hour** to keep your local storage clean.

---

## 🛠️ Quick Start & Installation

### 📋 Prerequisites
Ensure you have **Python 3.8+** installed on your system.

### 1. Clone & Set Up Directory
```bash
git clone https://github.com/yourusername/tunexa.git
cd tunexa
```

### 2. Install Dependencies
Tunexa will automatically resolve and package `yt-dlp` and `ffmpeg` paths. Run the following to install all Python requirements:
```bash
pip install -r requirements.txt
```

### 3. Start the Backend Server 🚀
Launch the FastAPI uvicorn server in your terminal:
```bash
uvicorn main:app --reload --port 8000
```

### 4. Run the Web Interface 🌐
Open your browser and navigate to:
```url
http://localhost:8000/ui/index.html
```

---

## 🎨 Tech Stack
*   **Frontend**: Vanilla HTML5, CSS3 Custom Properties (Modern HSL Glassmorphism, Responsive Grid System), Vanilla Modern JavaScript (ES6+).
*   **Backend**: Python, FastAPI, Uvicorn, Spotipy, yt-dlp, FFmpeg.

---

## 📜 Disclaimer
This application is designed **for personal use and educational purposes only**. Please respect the copyright terms of any media platform or artist before downloading content.

---

<p align="center">
  Made with ♥ by <b>Tushar Bhatt</b> ⚡
</p>
